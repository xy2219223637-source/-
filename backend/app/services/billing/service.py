from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from sqlalchemy import select, text

from app.auth import AuthContext
from app.config import get_settings
from app.database import SessionLocal
from app.models import Tenant, TenantMember, User
from app.models.billing import CreditAccount, CreditLedger, MemberQuota, BillingAuditEvent, BillingIdempotency
from app.permissions import permission_codes
from app.services.billing.amounts import MAX_INT, amount, credits, fail
from app.services.feishu_logging import trace_fields

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def key_uuid(key: str) -> str:
    try:
        if len(key) != 36:
            raise ValueError
        return str(uuid.UUID(key))
    except (ValueError, TypeError, AttributeError):
        fail("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 必须是 UUID", 422)


def reason_text(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
        fail("INVALID_REASON", "原因不能为空且不得超过200字", 400)
    return value.strip()


def utc_iso(value):
    # SQLite drops timezone metadata; stored timestamps in this domain are UTC.
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


class BillingService:
    def __init__(self, sessions=None, settings=None, clock=None):
        self.sessions = sessions or SessionLocal
        self.settings = settings or get_settings()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def transaction(self):
        """Every write owns its session; SQLite cannot emulate PG row locks."""
        with self.sessions() as db:
            try:
                if db.get_bind().dialect.name == "sqlite":
                    db.execute(text("PRAGMA foreign_keys=ON"))
                    db.execute(text("BEGIN IMMEDIATE"))
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def authorize(self, db, auth: AuthContext, permission=None, platform=False):
        user = db.get(User, auth.user_id)
        tenant = db.get(Tenant, auth.tenant_id)
        member = db.scalar(select(TenantMember).where(TenantMember.tenant_id == auth.tenant_id,
                                                    TenantMember.user_id == auth.user_id).with_for_update())
        if not user or user.status != "active" or not tenant or tenant.status != "active" or not member or member.status != "active":
            fail("UNAUTHENTICATED", "当前身份不可用", 401)
        if platform and not user.is_platform_admin:
            fail("FORBIDDEN", "仅平台管理员可操作", 403)
        if permission:
            codes = permission_codes(db, member)
            if "*" not in codes and permission not in codes:
                fail("FORBIDDEN", "无此计费权限", 403)
        return member

    def tenant_lock(self, db, tenant_id, *, active=True):
        # Lock the existing tenant first to serialize first-account creation too.
        tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if not tenant or (active and tenant.status != "active"):
            fail("ENTERPRISE_NOT_FOUND", "企业不存在或不可操作", 404)
        return tenant

    def account(self, db, tenant_id):
        account = db.scalar(select(CreditAccount).where(CreditAccount.tenant_id == tenant_id).with_for_update())
        if account is None:
            account = CreditAccount(tenant_id=tenant_id, balance_microcredits=0, reserved_microcredits=0)
            db.add(account)
            db.flush()
        return account

    def audit(self, db, tenant_id, actor_id, action, resource_id, changes=None, reason="", internal=False):
        db.add(BillingAuditEvent(tenant_id=tenant_id, actor_type="SERVICE" if internal else "USER", actor_id=actor_id,
                                action=action, resource_id=resource_id, changes_json=changes or {}, reason=reason,
                                trace_id=trace_fields().get("request_id", "")))

    def quota(self, db, tenant_id, user_id, *, create=True):
        member = db.scalar(select(TenantMember).where(TenantMember.tenant_id == tenant_id,
                                                     TenantMember.user_id == user_id).with_for_update())
        user = db.get(User, user_id)
        if not member or member.status != "active" or not user or user.status != "active":
            fail("RESOURCE_NOT_FOUND", "企业成员不存在或已停用", 404)
        period = self.clock().astimezone(SHANGHAI).strftime("%Y-%m")
        row = db.scalar(select(MemberQuota).where(MemberQuota.tenant_id == tenant_id, MemberQuota.user_id == user_id,
                                                 MemberQuota.period_key == period).with_for_update())
        if row is None and create:
            previous = db.scalar(select(MemberQuota).where(MemberQuota.tenant_id == tenant_id,
                                                          MemberQuota.user_id == user_id,
                                                          MemberQuota.period_key < period).order_by(MemberQuota.period_key.desc()).limit(1))
            if previous:
                row = MemberQuota(tenant_id=tenant_id, user_id=user_id, period_key=period,
                                  limit_microcredits=previous.limit_microcredits, used_microcredits=0, reserved_microcredits=0)
                db.add(row)
                db.flush()
                self.audit(db, tenant_id, "billing", "QUOTA_ROLLOVER", row.id,
                           {"from_period": previous.period_key, "to_period": period, "limit": row.limit_microcredits}, internal=True)
        return row

    @staticmethod
    def quota_result(row, user_id=None):
        return {"user_id": row.user_id if row else user_id, "authorized": row is not None,
                "period_key": row.period_key if row else None,
                "limit_credits": credits(row.limit_microcredits if row else 0),
                "used_credits": credits(row.used_microcredits if row else 0),
                "reserved_credits": credits(row.reserved_microcredits if row else 0),
                "remaining_credits": credits(row.limit_microcredits - row.used_microcredits - row.reserved_microcredits if row else 0)}

    def grant(self, auth, tenant_id, key, value, reason):
        key, reason = key_uuid(key), reason_text(reason)
        count = amount(value, maximum=amount(self.settings.billing_grant_max_credits))
        request_digest = digest({"tenant_id": tenant_id, "amount": count, "reason": reason})
        with self.transaction() as db:
            self.tenant_lock(db, tenant_id)
            self.authorize(db, auth, platform=True)
            prior = db.scalar(select(BillingIdempotency).where(BillingIdempotency.operation == "GRANT",
                              BillingIdempotency.tenant_id == tenant_id, BillingIdempotency.key == key))
            if prior:
                if prior.request_digest != request_digest:
                    fail("IDEMPOTENCY_CONFLICT", "同一幂等键的发放参数不同")
                return prior.response_json, True
            account = self.account(db, tenant_id)
            before = account.balance_microcredits
            if before > MAX_INT - count:
                fail("INVALID_GRANT_AMOUNT", "企业累计余额超出范围", 422)
            account.balance_microcredits += count
            ledger = CreditLedger(tenant_id=tenant_id, account_id=account.id, event_type="GRANT",
                                  delta_microcredits=count, balance_before_microcredits=before,
                                  balance_after_microcredits=account.balance_microcredits,
                                  business_key=f"GRANT:{tenant_id}:{key}", actor_type="USER", actor_id=auth.user_id, reason=reason)
            db.add(ledger)
            db.flush()
            result = {"grant_id": ledger.id, "ledger_id": ledger.id, "tenant_id": tenant_id, "amount_credits": credits(count),
                      "balance_after_credits": credits(account.balance_microcredits), "created_at": utc_iso(ledger.created_at)}
            self.audit(db, tenant_id, auth.user_id, "GRANT", ledger.id,
                       {"before": before, "after": account.balance_microcredits, "idempotency_key": key}, reason)
            db.add(BillingIdempotency(operation="GRANT", tenant_id=tenant_id, key=key,
                                     request_digest=request_digest, actor_id=auth.user_id, response_json=result))
            return result, False

    def revoke(self, auth, tenant_id, key, value, reason):
        key, reason = key_uuid(key), reason_text(reason)
        count = amount(value, maximum=amount(self.settings.billing_grant_max_credits))
        request_digest = digest({"tenant_id": tenant_id, "amount": count, "reason": reason})
        with self.transaction() as db:
            self.tenant_lock(db, tenant_id)
            self.authorize(db, auth, platform=True)
            prior = db.scalar(select(BillingIdempotency).where(BillingIdempotency.operation == "REVOKE",
                              BillingIdempotency.tenant_id == tenant_id, BillingIdempotency.key == key))
            if prior:
                if prior.request_digest != request_digest:
                    fail("IDEMPOTENCY_CONFLICT", "同一幂等键的扣回参数不同")
                return prior.response_json, True
            account = self.account(db, tenant_id)
            before = account.balance_microcredits
            if count > before - account.reserved_microcredits:
                fail("INSUFFICIENT_AVAILABLE_CREDITS", "扣回积分不能超过企业可用积分（余额减预占）")
            account.balance_microcredits -= count
            ledger = CreditLedger(tenant_id=tenant_id, account_id=account.id, event_type="REVOKE",
                                  delta_microcredits=-count, balance_before_microcredits=before,
                                  balance_after_microcredits=account.balance_microcredits,
                                  business_key=f"REVOKE:{tenant_id}:{key}", actor_type="USER", actor_id=auth.user_id, reason=reason)
            db.add(ledger)
            db.flush()
            result = {"revoke_id": ledger.id, "ledger_id": ledger.id, "tenant_id": tenant_id, "amount_credits": credits(count),
                      "balance_after_credits": credits(account.balance_microcredits), "created_at": utc_iso(ledger.created_at)}
            self.audit(db, tenant_id, auth.user_id, "REVOKE", ledger.id,
                       {"before": before, "after": account.balance_microcredits, "idempotency_key": key}, reason)
            db.add(BillingIdempotency(operation="REVOKE", tenant_id=tenant_id, key=key,
                                     request_digest=request_digest, actor_id=auth.user_id, response_json=result))
            return result, False

    def set_quota(self, auth, user_id, value, reason):
        count, reason = amount(value, zero=True, code="INVALID_QUOTA"), reason_text(reason)
        with self.transaction() as db:
            self.tenant_lock(db, auth.tenant_id)
            self.account(db, auth.tenant_id)
            self.authorize(db, auth, "billing.quota.manage")
            row = self.quota(db, auth.tenant_id, user_id)
            if row is None:
                row = MemberQuota(tenant_id=auth.tenant_id, user_id=user_id,
                                  period_key=self.clock().astimezone(SHANGHAI).strftime("%Y-%m"),
                                  limit_microcredits=0, used_microcredits=0, reserved_microcredits=0)
                db.add(row)
                db.flush()
            before = row.limit_microcredits
            if count < row.used_microcredits + row.reserved_microcredits:
                fail("QUOTA_BELOW_COMMITTED", "限额不能低于已用与预占之和")
            row.limit_microcredits = count
            self.audit(db, auth.tenant_id, auth.user_id, "QUOTA_SET", row.id, {"before": before, "after": count}, reason)
            return self.quota_result(row)

    def own_quota(self, auth):
        with self.transaction() as db:
            self.tenant_lock(db, auth.tenant_id)
            self.authorize(db, auth)
            return self.quota_result(self.quota(db, auth.tenant_id, auth.user_id), auth.user_id)

    def balance(self, auth, tenant_id=None):
        with self.sessions() as db:
            self.authorize(db, auth, None if tenant_id else "billing.view", platform=tenant_id is not None)
            tenant_id = tenant_id or auth.tenant_id
            if not db.get(Tenant, tenant_id):
                fail("ENTERPRISE_NOT_FOUND", "企业不存在", 404)
            account = db.scalar(select(CreditAccount).where(CreditAccount.tenant_id == tenant_id))
            balance, reserved = (account.balance_microcredits, account.reserved_microcredits) if account else (0, 0)
            return {"balance_credits": credits(balance), "reserved_credits": credits(reserved), "available_credits": credits(balance-reserved)}

    def members(self, auth, page=1, page_size=20):
        with self.transaction() as db:
            self.tenant_lock(db, auth.tenant_id)
            self.authorize(db, auth, "billing.view")
            query = select(TenantMember).where(TenantMember.tenant_id == auth.tenant_id, TenantMember.status == "active")
            from sqlalchemy import func
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = db.scalars(query.order_by(TenantMember.user_id).offset((page-1)*page_size).limit(page_size)).all()
            items = []
            for member in rows:
                user = db.get(User, member.user_id)
                if user.status != "active":
                    continue
                items.append({**self.quota_result(self.quota(db, auth.tenant_id, member.user_id), member.user_id), "name": user.name})
            return {"items": items, "total": total, "page": page, "page_size": page_size}

    def ledger(self, auth, tenant_id=None, *, grants=False, page=1, page_size=20, date_from=None, date_to=None, actor_id=None, adjustments=False):
        from sqlalchemy import func
        with self.sessions() as db:
            self.authorize(db, auth, None if grants else "billing.view", platform=grants)
            tenant_id = tenant_id if grants else auth.tenant_id
            tenant = db.get(Tenant, tenant_id)
            if not tenant:
                fail("ENTERPRISE_NOT_FOUND", "企业不存在", 404)
            end = date_to or self.clock()
            start = date_from or end.astimezone(SHANGHAI).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start > end or end-start > timedelta(days=93):
                fail("INVALID_DATE_RANGE", "查询时间范围最多93天", 422)
            query = select(CreditLedger).where(CreditLedger.tenant_id == tenant_id, CreditLedger.created_at >= start, CreditLedger.created_at <= end)
            if grants:
                query = query.where(CreditLedger.event_type.in_(["GRANT", "REVOKE"]) if adjustments else CreditLedger.event_type == "GRANT")
            if actor_id:
                query = query.where(CreditLedger.actor_id == actor_id)
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = db.scalars(query.order_by(CreditLedger.created_at.desc(), CreditLedger.id).offset((page-1)*page_size).limit(page_size)).all()
            return {"items": [{"id": row.id, "event_type": row.event_type, "delta_credits": credits(row.delta_microcredits),
                               "balance_after_credits": credits(row.balance_after_microcredits), "reason": row.reason,
                               "actor_id": row.actor_id, "order_id": row.order_id, "created_at": utc_iso(row.created_at)} for row in rows],
                    "total": total, "page": page, "page_size": page_size}


class GrantEnterpriseCreditsService(BillingService):
    """Named entrypoint shared by HTTP and any controlled operational client."""


def get_billing_service():
    # Lazy import avoids mixing provider initialization into accounting.
    from app.services.billing.orders import BillingOrderService
    return BillingOrderService()
