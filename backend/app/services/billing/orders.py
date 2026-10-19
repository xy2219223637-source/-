"""Trusted billing engine. HTTP callers never provide usage or settlement amounts."""
from __future__ import annotations

from datetime import timedelta
import uuid

from sqlalchemy import func, select, text

from app.models.billing import BillingOrder, BillingPriceRule, CreditLedger, MemberQuota
from app.services.billing.amounts import amount, calculate, credits, fail
from app.services.billing.service import GrantEnterpriseCreditsService, digest, key_uuid, utc_iso
from app.services.billing.model_pricing import ModelPricingService

FEATURES = {"mock_fixed": "FIXED", "mock_text": "USAGE"}


class KnownNoUsage(Exception):
    """Adapter positively proves no business success / billable execution."""


class MockAI:
    def execute(self, feature_code):
        # No network clients, prompt logging or client-controlled token counts.
        if feature_code == "mock_fixed":
            return {"message": "按次测试完成"}, {}
        return {"message": "Token 测试完成"}, {"input_tokens": 2000, "output_tokens": 1000}


class BillingOrderService(ModelPricingService):
    def price_lock(self, db):
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(724913221)"))

    def create_price(self, auth, payload):
        if payload.billing_mode != FEATURES[payload.feature_code]:
            fail("INVALID_PRICE", "功能与计费模式不匹配", 422)
        fixed = None
        rates = []
        if payload.billing_mode == "FIXED":
            if payload.metrics or payload.fixed_credits is None:
                fail("INVALID_PRICE", "按次规则需固定积分且不能含计量项", 422)
            fixed = amount(payload.fixed_credits)
        else:
            if payload.fixed_credits is not None or {r.metric for r in payload.metrics} != {"input_tokens", "output_tokens"} or len(payload.metrics) != 2:
                fail("INVALID_PRICE", "Token 规则需且仅需输入/输出计量项", 422)
            for rate in payload.metrics:
                amount(rate.credits_per_unit)
                rates.append(rate.model_dump())
        with self.transaction() as db:
            self.price_lock(db)
            self.authorize(db, auth, platform=True)
            if db.scalar(select(BillingPriceRule).where(BillingPriceRule.pricing_scope_key == payload.feature_code, BillingPriceRule.version == payload.version)):
                fail("PRICE_VERSION_EXISTS", "费率版本已存在")
            row = BillingPriceRule(pricing_scope_key=payload.feature_code, feature_code=payload.feature_code, version=payload.version,
                                   billing_mode=payload.billing_mode, fixed_microcredits=fixed, metric_rates_json=rates, created_by=auth.user_id)
            db.add(row); db.flush()
            self.audit(db, auth.tenant_id, auth.user_id, "PRICE_CREATE", row.id, {"scope": row.pricing_scope_key, "version": row.version})
            return self.price_result(row)

    @staticmethod
    def price_result(row):
        return {"id": row.id, "feature_code": row.feature_code, "pricing_scope_key": row.pricing_scope_key,
                "pricing_version": row.version, "billing_mode": row.billing_mode, "status": row.status,
                "fixed_microcredits": row.fixed_microcredits, "fixed_credits": credits(row.fixed_microcredits) if row.fixed_microcredits is not None else None, "metrics": row.metric_rates_json}

    def activate_price(self, auth, price_id):
        with self.transaction() as db:
            self.price_lock(db)
            self.authorize(db, auth, platform=True)
            row = db.get(BillingPriceRule, price_id)
            if not row:
                fail("RESOURCE_NOT_FOUND", "费率不存在", 404)
            if row.status == "ACTIVE":
                return self.price_result(row)
            if row.status != "DRAFT":
                fail("INVALID_PRICE_STATE", "旧费率不能重新激活，请创建新版本")
            current = db.scalar(select(BillingPriceRule).where(BillingPriceRule.pricing_scope_key == row.pricing_scope_key, BillingPriceRule.status == "ACTIVE"))
            if current:
                current.status = "RETIRED"; db.flush()
            row.status = "ACTIVE"
            self.audit(db, auth.tenant_id, auth.user_id, "PRICE_ACTIVATE", row.id, {"previous_id": current.id if current else None})
            return self.price_result(row)

    def list_prices(self, auth, page=1, page_size=20):
        with self.sessions() as db:
            self.authorize(db, auth, platform=True)
            total = db.scalar(select(func.count()).select_from(BillingPriceRule))
            rows = db.scalars(select(BillingPriceRule).order_by(BillingPriceRule.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
            return {"items": [self.price_result(r) for r in rows], "total": total}

    def price_summary(self, auth):
        with self.transaction() as db:
            self.price_lock(db)
            self.authorize(db, auth, platform=True)
            items = []
            for feature, mode in FEATURES.items():
                active = db.scalar(select(BillingPriceRule).where(BillingPriceRule.pricing_scope_key == feature, BillingPriceRule.status == "ACTIVE"))
                latest = db.scalar(select(func.max(BillingPriceRule.version)).where(BillingPriceRule.pricing_scope_key == feature)) or 0
                items.append({"feature_code": feature, "billing_mode": mode, "active": self.price_result(active) if active else None, "next_version": latest + 1})
            return {"items": items}

    def estimate(self, feature_code, maximum_usage):
        """Trusted preview only; reserve obtains its own durable price snapshot."""
        if feature_code not in FEATURES:
            fail("UNPRICED_FEATURE", "未登记计费功能", 422)
        with self.sessions() as db:
            rule = db.scalar(select(BillingPriceRule).where(BillingPriceRule.pricing_scope_key == feature_code, BillingPriceRule.status == "ACTIVE"))
            if rule is None:
                fail("UNPRICED_FEATURE", "功能没有生效计费规则", 422)
            snapshot = self.price_result(rule)
            return {"estimated_credits": credits(calculate(snapshot, maximum_usage)), "pricing_snapshot": snapshot}

    @staticmethod
    def order_result(row):
        return {"id": row.id, "request_id": row.request_id, "feature_code": row.feature_code, "status": row.status,
                "period_key": row.period_key, "charged_credits": credits(row.charged_microcredits),
                "reserved_credits": credits(row.reserved_microcredits if row.status == "RESERVED" else 0),
                "reconciliation_status": row.reconciliation_status,
                "result": row.result_json if row.status == "SETTLED" else None, "created_at": utc_iso(row.created_at)}

    def reserve(self, auth, request_id, feature_code, max_usage, business_digest):
        if feature_code not in FEATURES:
            fail("UNPRICED_FEATURE", "未登记计费功能", 422)
        fingerprint = digest({"tenant": auth.tenant_id, "user": auth.user_id, "feature": feature_code, "max_usage": max_usage, "business_digest": business_digest})
        with self.transaction() as db:
            self.tenant_lock(db, auth.tenant_id)
            account = self.account(db, auth.tenant_id)
            self.authorize(db, auth, "billing.mock.use")
            prior = db.scalar(select(BillingOrder).where(BillingOrder.request_id == request_id))
            if prior:
                if prior.tenant_id != auth.tenant_id or prior.user_id != auth.user_id:
                    fail("RESOURCE_NOT_FOUND", "订单不可见", 404)
                if prior.request_digest != fingerprint:
                    fail("IDEMPOTENCY_CONFLICT", "请求键已绑定其他业务参数")
                return self.order_result(prior)
            rule = db.scalar(select(BillingPriceRule).where(BillingPriceRule.pricing_scope_key == feature_code, BillingPriceRule.status == "ACTIVE"))
            if rule is None:
                fail("UNPRICED_FEATURE", "功能没有生效计费规则", 422)
            snapshot = self.price_result(rule)
            reservation = calculate(snapshot, max_usage)
            quota = self.quota(db, auth.tenant_id, auth.user_id)
            if quota is None:
                fail("QUOTA_NOT_CONFIGURED", "成员尚未配置消费额度")
            if account.balance_microcredits - account.reserved_microcredits < reservation:
                fail("INSUFFICIENT_CREDITS", "企业可用积分不足")
            if quota.limit_microcredits - quota.used_microcredits - quota.reserved_microcredits < reservation:
                fail("QUOTA_EXCEEDED", "成员本月剩余额度不足")
            account.reserved_microcredits += reservation
            quota.reserved_microcredits += reservation
            db.flush()
            row = BillingOrder(request_id=request_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
                               quota_id=quota.id, period_key=quota.period_key, feature_code=feature_code,
                               pricing_rule_id=rule.id, pricing_snapshot_json=snapshot, request_digest=fingerprint,
                               reserved_microcredits=reservation, max_usage_json=max_usage,
                               expires_at=self.clock()+timedelta(seconds=self.settings.billing_reservation_seconds))
            db.add(row); db.flush()
            self.audit(db, auth.tenant_id, auth.user_id, "RESERVE", row.id, {"reserved": reservation, "period": row.period_key})
            return self.order_result(row)

    def locked_order(self, db, order_id):
        # Tenant is immutable. Discovery read is followed by lock + fresh state read.
        tenant_id = db.scalar(select(BillingOrder.tenant_id).where(BillingOrder.id == order_id))
        if tenant_id is None:
            fail("RESOURCE_NOT_FOUND", "计费订单不存在", 404)
        self.tenant_lock(db, tenant_id, active=False)
        account = self.account(db, tenant_id)
        row = db.scalar(select(BillingOrder).where(BillingOrder.id == order_id).with_for_update())
        quota = db.get(MemberQuota, row.quota_id)
        return row, account, quota

    def claim(self, order_id):
        with self.transaction() as db:
            row, _, _ = self.locked_order(db, order_id)
            if row.status != "RESERVED" or row.execution_status != "NOT_STARTED" or row.reconciliation_status != "NONE":
                return False
            row.execution_status = "STARTED"
            self.audit(db, row.tenant_id, "mock-ai", "EXECUTION_START", row.id, internal=True)
            return True

    def record_outcome(self, order_id, usage, result):
        with self.transaction() as db:
            row, _, _ = self.locked_order(db, order_id)
            if row.status != "RESERVED" or row.execution_status != "STARTED":
                fail("INVALID_ORDER_STATE", "订单不可记录执行结果")
            calculate(row.pricing_snapshot_json, usage)
            row.usage_json, row.result_json = usage, result
            row.execution_status = "RESULT_READY"
            self.audit(db, row.tenant_id, "mock-ai", "RESULT_READY", row.id, {"usage": usage}, internal=True)

    def settle(self, order_id, usage, result):
        """Internal API: caller is a trusted adapter or recovery service, never HTTP."""
        fingerprint = digest({"usage": usage, "result": result})
        overflow = False
        with self.transaction() as db:
            row, account, quota = self.locked_order(db, order_id)
            if row.status == "SETTLED":
                if row.settlement_digest != fingerprint:
                    fail("IDEMPOTENCY_CONFLICT", "重复结算的用量或结果不一致")
                return self.order_result(row)
            if row.status != "RESERVED":
                fail("INVALID_ORDER_STATE", "已取消的订单不能结算")
            charge = calculate(row.pricing_snapshot_json, usage)
            if charge > row.reserved_microcredits or any(usage[k] > row.max_usage_json[k] for k in usage):
                row.reconciliation_status, row.error_code = "NEEDS_REVIEW", "USAGE_EXCEEDS_RESERVATION"
                self.audit(db, row.tenant_id, "billing", "USAGE_LIMIT_EXCEEDED", row.id, internal=True)
                overflow = True
            else:
                before = account.balance_microcredits
                account.balance_microcredits -= charge
                account.reserved_microcredits -= row.reserved_microcredits
                quota.used_microcredits += charge
                quota.reserved_microcredits -= row.reserved_microcredits
                row.status, row.execution_status, row.reconciliation_status = "SETTLED", "RESULT_READY", "RESOLVED"
                row.charged_microcredits, row.usage_json, row.result_json = charge, usage, result
                row.settlement_digest, row.settled_at = fingerprint, self.clock()
                db.add(CreditLedger(tenant_id=row.tenant_id, account_id=account.id, order_id=row.id, event_type="CHARGE",
                                   delta_microcredits=-charge, balance_before_microcredits=before,
                                   balance_after_microcredits=account.balance_microcredits, business_key=f"CHARGE:{row.id}",
                                   actor_type="USER", actor_id=row.user_id, reason=row.feature_code))
                self.audit(db, row.tenant_id, "billing", "SETTLE", row.id, {"charge": charge, "released": row.reserved_microcredits-charge}, internal=True)
                result_value = self.order_result(row)
        if overflow:
            fail("USAGE_EXCEEDS_RESERVATION", "用量超预占，保留额度待核对")
        return result_value

    def cancel(self, order_id, *, no_usage_proven=False):
        with self.transaction() as db:
            row, account, quota = self.locked_order(db, order_id)
            if row.status == "CANCELED":
                return self.order_result(row)
            if row.status != "RESERVED":
                fail("INVALID_ORDER_STATE", "已结算订单不能取消")
            if row.execution_status != "NOT_STARTED" and not no_usage_proven:
                fail("EXECUTION_UNCERTAIN", "不能证明无用量，禁止取消")
            account.reserved_microcredits -= row.reserved_microcredits
            quota.reserved_microcredits -= row.reserved_microcredits
            row.status, row.reconciliation_status, row.error_code = "CANCELED", "RESOLVED", "NO_USAGE_PROVEN"
            row.settled_at = self.clock()
            self.audit(db, row.tenant_id, "billing", "CANCEL", row.id, {"released": row.reserved_microcredits}, internal=True)
            return self.order_result(row)

    def mark_review(self, order_id, code="EXECUTION_UNCERTAIN"):
        with self.transaction() as db:
            row, _, _ = self.locked_order(db, order_id)
            if row.status == "RESERVED" and row.reconciliation_status != "NEEDS_REVIEW":
                row.reconciliation_status, row.error_code = "NEEDS_REVIEW", code
                self.audit(db, row.tenant_id, "billing", "NEEDS_REVIEW", row.id, {"code": code}, internal=True)

    def run_mock(self, auth, feature_code, key, text_value, adapter=None):
        key = key_uuid(key)
        request_id = uuid.uuid5(uuid.NAMESPACE_URL, f"billing:{auth.tenant_id}:{auth.user_id}:{feature_code}:{key}").hex
        maximum = {} if feature_code == "mock_fixed" else {"input_tokens": 3000, "output_tokens": 2000}
        order = self.reserve(auth, request_id, feature_code, maximum, digest({"text": text_value}))
        if order["status"] == "CANCELED":
            fail("ORDER_CANCELED", "请求已取消，请使用新的请求键")
        if order["status"] == "SETTLED":
            return order, False
        if not self.claim(order["id"]):
            return order, True
        try:
            result, usage = (adapter or MockAI()).execute(feature_code)
        except KnownNoUsage:
            self.cancel(order["id"], no_usage_proven=True)
            fail("MOCK_FAILED", "业务未完成，预占已释放", 503)
        except Exception:
            self.mark_review(order["id"])
            fail("EXECUTION_UNCERTAIN", "执行状态待核对，预占保留", 503)
        try:
            self.record_outcome(order["id"], usage, result)
            return self.settle(order["id"], usage, result), False
        except Exception:
            self.mark_review(order["id"], "SETTLEMENT_PENDING")
            raise

    def own_orders(self, auth, page=1, page_size=20):
        with self.sessions() as db:
            self.authorize(db, auth)
            query = select(BillingOrder).where(BillingOrder.tenant_id == auth.tenant_id, BillingOrder.user_id == auth.user_id)
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = db.scalars(query.order_by(BillingOrder.created_at.desc(), BillingOrder.id).offset((page-1)*page_size).limit(page_size)).all()
            return {"items": [self.order_result(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    def reconcile(self, *, recover=False, batch_size=100):
        with self.sessions() as db:
            eligible = BillingOrder.reconciliation_status == "NONE"
            if recover:
                from sqlalchemy import or_
                eligible = or_(eligible, BillingOrder.execution_status.in_(["NOT_STARTED", "RESULT_READY"]))
            ids = db.scalars(select(BillingOrder.id).where(BillingOrder.status == "RESERVED", BillingOrder.expires_at <= self.clock(), eligible)
                             .order_by(BillingOrder.expires_at).limit(batch_size)).all()
        outcomes = []
        for order_id in ids:
            self.mark_review(order_id, "RESERVATION_TIMEOUT")
            with self.sessions() as db:
                row = db.get(BillingOrder, order_id)
                status, execution, usage, result = row.status, row.execution_status, row.usage_json, row.result_json
            if recover and status == "RESERVED":
                if execution == "NOT_STARTED":
                    self.cancel(order_id)
                elif execution == "RESULT_READY":
                    self.settle(order_id, usage, result)
            outcomes.append(order_id)
        return {"scanned": len(outcomes), "order_ids": outcomes}

    def check_accounts(self):
        from app.models.billing import CreditAccount
        differences = []
        with self.sessions() as db:
            # Repeatable read ensures concurrent committed billing cannot mimic drift.
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            else:
                db.execute(text("BEGIN"))
            for account in db.scalars(select(CreditAccount)):
                total = db.scalar(select(func.coalesce(func.sum(CreditLedger.delta_microcredits), 0)).where(CreditLedger.tenant_id == account.tenant_id))
                reserved = db.scalar(select(func.coalesce(func.sum(BillingOrder.reserved_microcredits), 0)).where(BillingOrder.tenant_id == account.tenant_id, BillingOrder.status == "RESERVED"))
                if (account.balance_microcredits, account.reserved_microcredits) != (total, reserved):
                    differences.append({"tenant_id": account.tenant_id, "kind": "ACCOUNT_MISMATCH"})
            for quota in db.scalars(select(MemberQuota)):
                used = db.scalar(select(func.coalesce(func.sum(BillingOrder.charged_microcredits), 0)).where(BillingOrder.quota_id == quota.id, BillingOrder.status == "SETTLED"))
                reserved = db.scalar(select(func.coalesce(func.sum(BillingOrder.reserved_microcredits), 0)).where(BillingOrder.quota_id == quota.id, BillingOrder.status == "RESERVED"))
                if (quota.used_microcredits, quota.reserved_microcredits) != (used, reserved):
                    differences.append({"quota_id": quota.id, "kind": "QUOTA_MISMATCH"})
        return {"ok": not differences, "differences": differences}
