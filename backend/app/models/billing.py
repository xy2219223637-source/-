"""Shared enterprise credits; all tables require the explicit billing migration."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def now():
    return datetime.now(timezone.utc)


def identifier():
    return uuid.uuid4().hex


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("balance_microcredits >= reserved_microcredits AND reserved_microcredits >= 0", name="ck_credit_account_balance"),
        {"info": {"explicit_migration_required": True}},
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), unique=True)
    balance_microcredits: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_microcredits: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MemberQuota(Base):
    __tablename__ = "member_quotas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "period_key"),
        UniqueConstraint("tenant_id", "user_id", "period_key", "id"),
        CheckConstraint("period_type = 'MONTHLY'", name="ck_member_quota_period"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"], ondelete="RESTRICT"),
        CheckConstraint("limit_microcredits >= used_microcredits + reserved_microcredits AND used_microcredits >= 0 AND reserved_microcredits >= 0", name="ck_member_quota_balance"),
        {"info": {"explicit_migration_required": True}},
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64))
    period_key: Mapped[str] = mapped_column(String(7))
    period_type: Mapped[str] = mapped_column(String(10), default="MONTHLY")
    limit_microcredits: Mapped[int] = mapped_column(BigInteger, default=0)
    used_microcredits: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_microcredits: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BillingPriceRule(Base):
    __tablename__ = "billing_price_rules"
    __table_args__ = (
        UniqueConstraint("pricing_scope_key", "version"),
        Index("uq_billing_active_scope", "pricing_scope_key", unique=True,
              postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'")),
        CheckConstraint("version > 0 AND status IN ('DRAFT','ACTIVE','RETIRED')", name="ck_billing_rule_state"),
        CheckConstraint("(billing_mode = 'FIXED' AND fixed_microcredits IS NOT NULL AND fixed_microcredits > 0) OR (billing_mode = 'USAGE' AND fixed_microcredits IS NULL)", name="ck_billing_rule_mode"),
        {"info": {"explicit_migration_required": True}},
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    pricing_scope_key: Mapped[str] = mapped_column(String(100))
    feature_code: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    billing_mode: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    fixed_microcredits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metric_rates_json: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BillingOrder(Base):
    __tablename__ = "billing_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "user_id", "period_key", "quota_id"],
                             ["member_quotas.tenant_id", "member_quotas.user_id", "member_quotas.period_key", "member_quotas.id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('RESERVED','SETTLED','CANCELED')", name="ck_billing_order_state"),
        CheckConstraint("reserved_microcredits >= 0 AND charged_microcredits >= 0 AND charged_microcredits <= reserved_microcredits", name="ck_billing_order_amount"),
        Index("ix_billing_order_owner", "tenant_id", "user_id", "created_at"),
        Index("ix_billing_order_expiry", "status", "expires_at"),
        {"info": {"explicit_migration_required": True}},
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    user_id: Mapped[str] = mapped_column(String(64))
    quota_id: Mapped[str] = mapped_column(String(32))
    period_key: Mapped[str] = mapped_column(String(7))
    feature_code: Mapped[str] = mapped_column(String(100))
    pricing_rule_id: Mapped[str] = mapped_column(ForeignKey("billing_price_rules.id", ondelete="RESTRICT"))
    pricing_snapshot_json: Mapped[dict] = mapped_column(JSON)
    request_digest: Mapped[str] = mapped_column(String(64))
    settlement_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(10), default="RESERVED")
    execution_status: Mapped[str] = mapped_column(String(20), default="NOT_STARTED")
    reconciliation_status: Mapped[str] = mapped_column(String(20), default="NONE")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    reserved_microcredits: Mapped[int] = mapped_column(BigInteger)
    charged_microcredits: Mapped[int] = mapped_column(BigInteger, default=0)
    usage_json: Mapped[dict] = mapped_column(JSON, default=dict)
    max_usage_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "account_id"], ["credit_accounts.tenant_id", "credit_accounts.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "order_id"], ["billing_orders.tenant_id", "billing_orders.id"], ondelete="RESTRICT"),
        CheckConstraint("(event_type = 'GRANT' AND delta_microcredits > 0 AND order_id IS NULL) OR (event_type = 'REVOKE' AND delta_microcredits < 0 AND order_id IS NULL) OR (event_type = 'CHARGE' AND delta_microcredits <= 0 AND order_id IS NOT NULL)", name="ck_credit_ledger_direction"),
        CheckConstraint("balance_after_microcredits = balance_before_microcredits + delta_microcredits AND balance_before_microcredits >= 0 AND balance_after_microcredits >= 0", name="ck_credit_ledger_equation"),
        Index("ix_credit_ledger_tenant_created", "tenant_id", "created_at"),
        {"info": {"explicit_migration_required": True}},
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(10))
    delta_microcredits: Mapped[int] = mapped_column(BigInteger)
    balance_before_microcredits: Mapped[int] = mapped_column(BigInteger)
    balance_after_microcredits: Mapped[int] = mapped_column(BigInteger)
    business_key: Mapped[str] = mapped_column(String(160), unique=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BillingIdempotency(Base):
    __tablename__ = "billing_idempotency"
    __table_args__ = (UniqueConstraint("operation", "tenant_id", "key"), {"info": {"explicit_migration_required": True}})
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    operation: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    key: Mapped[str] = mapped_column(String(36))
    request_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BillingAuditEvent(Base):
    __tablename__ = "billing_audit_events"
    __table_args__ = ({"info": {"explicit_migration_required": True}},)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(200), default="")
    changes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BillingSchemaVersion(Base):
    __tablename__ = "billing_schema_versions"
    __table_args__ = ({"info": {"explicit_migration_required": True}},)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


BILLING_TABLES = (CreditAccount.__table__, MemberQuota.__table__, BillingPriceRule.__table__, BillingOrder.__table__,
                  CreditLedger.__table__, BillingIdempotency.__table__, BillingAuditEvent.__table__, BillingSchemaVersion.__table__)
