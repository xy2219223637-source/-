from __future__ import annotations

from datetime import UTC, datetime
import uuid

from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models import QianchuanDeliveryPlan as Plan, QianchuanPlanOperation as Operation


class QianchuanPlanOperationService:
    """State protection only; this service performs no provider requests."""

    def __init__(self, auth_service):
        self.auth = auth_service
        self.settings = auth_service.settings

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def _require_write_gate(self, db, auth) -> None:
        self.auth.require_content_access(auth)
        if not (self.settings.qianchuan_enabled and self.settings.qianchuan_plan_enabled):
            raise HTTPException(404, "千川计划功能未启用")
        if not self.settings.qianchuan_plan_write_enabled:
            raise HTTPException(409, "千川计划写入未启用")
        bind = db.get_bind()
        if bind is None or not inspect(bind).has_table(Plan.__tablename__):
            raise HTTPException(503, "千川计划旁路表尚未完成显式迁移")

    @staticmethod
    def view(row: Operation) -> dict:
        return {key: getattr(row, key) for key in (
            "id", "plan_id", "operation_type", "status", "before_status", "after_status",
            "request_version", "result_certainty", "provider_code", "provider_request_id",
            "error_message", "created_at", "started_at", "completed_at",
        )}

    @staticmethod
    def _owned_plan(db, auth, plan_id: str) -> Plan:
        row = db.query(Plan).filter_by(
            id=plan_id, tenant_id=auth.tenant_id, created_by=auth.user_id).first()
        if not row:
            raise HTTPException(404, "未找到投放计划")
        return row

    @staticmethod
    def _owned_operation(db, auth, operation_id: str) -> Operation:
        row = db.query(Operation).filter_by(
            id=operation_id, tenant_id=auth.tenant_id, created_by=auth.user_id).first()
        if not row:
            raise HTTPException(404, "未找到计划操作")
        return row

    @staticmethod
    def _same_request(row: Operation, plan_id: str, operation_type: str, version: int) -> bool:
        return (row.plan_id == plan_id and row.operation_type == operation_type
                and row.request_version == version)

    def _idempotent_existing(self, db, auth, *, key: str, plan_id: str,
                             operation_type: str, version: int) -> Operation | None:
        row = db.query(Operation).filter_by(
            tenant_id=auth.tenant_id, created_by=auth.user_id, idempotency_key=key).first()
        if row and not self._same_request(row, plan_id, operation_type, version):
            raise HTTPException(409, "幂等键已用于其他计划操作")
        return row

    def _reserve(self, db, auth, plan_id: str, payload, *, operation_type: str,
                 expected_status: str, claimed_status: str) -> dict:
        self._require_write_gate(db, auth)
        if payload.confirmed is not True:
            raise HTTPException(409, "必须明确确认计划操作")
        plan_id = str(plan_id)
        existing = self._idempotent_existing(
            db, auth, key=payload.idempotency_key, plan_id=plan_id,
            operation_type=operation_type, version=payload.operation_version)
        if existing:
            return self.view(existing)

        plan = self._owned_plan(db, auth, plan_id)
        if plan.operation_version != payload.operation_version:
            raise HTTPException(409, "计划版本已变化，请刷新后重试")
        if plan.local_status != expected_status:
            raise HTTPException(409, f"计划当前状态不允许执行 {operation_type}")

        operation = Operation(
            id=str(uuid.uuid4()), plan_id=plan.id, tenant_id=auth.tenant_id,
            created_by=auth.user_id, operation_type=operation_type, status="pending",
            before_status=expected_status, after_status=claimed_status,
            idempotency_key=payload.idempotency_key,
            request_version=payload.operation_version, result_certainty="not_started",
            request_snapshot={
                "schema_version": 1,
                "plan_id": plan.id,
                "operation_type": operation_type,
                "request_version": payload.operation_version,
                "plan": dict(plan.request_snapshot or {}),
            },
        )
        db.add(operation)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = self._idempotent_existing(
                db, auth, key=payload.idempotency_key, plan_id=plan_id,
                operation_type=operation_type, version=payload.operation_version)
            if existing:
                return self.view(existing)
            raise HTTPException(409, "计划操作发生并发冲突") from None

        changed = db.query(Plan).filter(
            Plan.id == plan.id,
            Plan.tenant_id == auth.tenant_id,
            Plan.created_by == auth.user_id,
            Plan.local_status == expected_status,
            Plan.operation_version == payload.operation_version,
        ).update({
            Plan.local_status: claimed_status,
            Plan.last_operation: operation_type,
            Plan.operation_version: payload.operation_version + 1,
            Plan.error_code: "",
            Plan.error_message: "",
            Plan.updated_at: self._now(),
        }, synchronize_session=False)
        if changed != 1:
            db.rollback()
            raise HTTPException(409, "计划已被其他操作更新，请刷新后重试")
        db.commit()
        db.refresh(operation)
        return self.view(operation)

    def reserve_submit(self, db, auth, plan_id: str, payload) -> dict:
        """Atomically claim a draft. A future executor may process the returned operation."""
        return self._reserve(
            db, auth, plan_id, payload, operation_type="create",
            expected_status="draft", claimed_status="submitting")

    def reserve_uncertain_recheck(self, db, auth, plan_id: str, payload) -> dict:
        """Claim a query-only reconciliation; it must never resend create."""
        return self._reserve(
            db, auth, plan_id, payload, operation_type="reconcile_create",
            expected_status="result_uncertain", claimed_status="syncing")

    def _record_result(self, db, auth, operation_id: str, *, operation_type: str,
                       target_status: str, operation_status: str,
                       certainty: str, platform_ad_id: str | None = None,
                       provider_request_id: str = "", error_code: str = "",
                       error_message: str = "") -> dict:
        self.auth.require_content_access(auth)
        operation = self._owned_operation(db, auth, str(operation_id))
        if operation.operation_type != operation_type:
            raise HTTPException(409, "操作类型与结果不匹配")
        plan = self._owned_plan(db, auth, operation.plan_id)

        if operation.status != "pending":
            if operation.status == operation_status and operation.after_status == target_status:
                return self.view(operation)
            raise HTTPException(409, "操作已经以其他结果结束")
        expected_plan_status = "submitting" if operation_type == "create" else "syncing"
        if (plan.local_status != expected_plan_status
                or plan.operation_version != operation.request_version + 1
                or plan.last_operation != operation_type):
            raise HTTPException(409, "计划状态或版本已变化，拒绝覆盖")
        if target_status == "submitted" and not platform_ad_id:
            raise HTTPException(422, "确认创建成功必须提供平台计划 ID")
        if platform_ad_id and (not platform_ad_id.isdigit() or len(platform_ad_id) > 20
                               or platform_ad_id.startswith("0")):
            raise HTTPException(422, "平台计划 ID 格式无效")

        now = self._now()
        changed_operation = db.query(Operation).filter(
            Operation.id == operation.id,
            Operation.status == "pending",
            Operation.request_version == operation.request_version,
        ).update({
            Operation.status: operation_status,
            Operation.after_status: target_status,
            Operation.result_certainty: certainty,
            Operation.provider_request_id: provider_request_id[:128],
            Operation.error_code: error_code[:64],
            Operation.error_message: error_message[:256],
            Operation.started_at: operation.started_at or now,
            Operation.completed_at: now,
        }, synchronize_session=False)
        changed_plan = db.query(Plan).filter(
            Plan.id == plan.id,
            Plan.tenant_id == auth.tenant_id,
            Plan.created_by == auth.user_id,
            Plan.local_status == expected_plan_status,
            Plan.operation_version == operation.request_version + 1,
            Plan.last_operation == operation_type,
        ).update({
            Plan.local_status: target_status,
            Plan.platform_ad_id: platform_ad_id if target_status == "submitted" else plan.platform_ad_id,
            Plan.provider_request_id: provider_request_id[:128],
            Plan.error_code: error_code[:64],
            Plan.error_message: error_message[:256],
            Plan.updated_at: now,
            Plan.last_synced_at: now if operation_type == "reconcile_create" else plan.last_synced_at,
        }, synchronize_session=False)
        if changed_operation != 1 or changed_plan != 1:
            db.rollback()
            raise HTTPException(409, "结果写入发生并发冲突")
        db.commit()
        refreshed = self._owned_operation(db, auth, operation.id)
        return self.view(refreshed)

    def record_submit_result(self, db, auth, operation_id: str, *, outcome: str,
                             platform_ad_id: str | None = None,
                             provider_request_id: str = "", error_code: str = "",
                             error_message: str = "") -> dict:
        states = {
            "created": ("submitted", "succeeded", "confirmed_created"),
            "not_created": ("failed", "failed", "confirmed_not_created"),
            "uncertain": ("result_uncertain", "result_uncertain", "unknown"),
        }
        if outcome not in states:
            raise HTTPException(422, "未知的提交结果")
        target, status, certainty = states[outcome]
        return self._record_result(
            db, auth, operation_id, operation_type="create",
            target_status=target, operation_status=status, certainty=certainty,
            platform_ad_id=platform_ad_id, provider_request_id=provider_request_id,
            error_code=error_code, error_message=error_message)

    def record_recheck_result(self, db, auth, operation_id: str, *, outcome: str,
                              platform_ad_id: str | None = None,
                              provider_request_id: str = "", error_code: str = "",
                              error_message: str = "") -> dict:
        states = {
            "found": ("submitted", "succeeded", "confirmed_created"),
            "confirmed_absent": ("failed", "failed", "confirmed_not_created"),
            "inconclusive": ("result_uncertain", "result_uncertain", "unknown"),
        }
        if outcome not in states:
            raise HTTPException(422, "未知的回查结果")
        target, status, certainty = states[outcome]
        return self._record_result(
            db, auth, operation_id, operation_type="reconcile_create",
            target_status=target, operation_status=status, certainty=certainty,
            platform_ad_id=platform_ad_id, provider_request_id=provider_request_id,
            error_code=error_code, error_message=error_message)
