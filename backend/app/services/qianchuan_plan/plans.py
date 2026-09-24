from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from fastapi import HTTPException
from sqlalchemy import inspect

from app.models import QianchuanDeliveryPlan as Plan
from app.services.qianchuan.client import PlatformError
from .access import QianchuanPlanAccess
from .client import QianchuanPlanClient


class QianchuanPlanService:
    """Local plan state only; this service never sends provider write requests."""

    def __init__(self, auth_service, plan_client: QianchuanPlanClient | None = None):
        self.auth = auth_service
        self.settings = auth_service.settings
        self.access = QianchuanPlanAccess(auth_service)
        self.client = plan_client or QianchuanPlanClient(auth_service.client)

    @staticmethod
    def view(row: Plan) -> dict:
        return {key: getattr(row, key) for key in (
            "id", "video_asset_id", "upload_id", "binding_id", "advertiser_id", "material_id",
            "product_id", "product_name", "aweme_id", "aweme_name", "name", "marketing_goal",
            "budget_minor", "roi_goal", "platform_ad_id", "local_status", "platform_status",
            "review_status", "delivery_status", "operation_version", "error_message", "created_at",
            "updated_at", "last_synced_at",
        )}

    def _require_write_gate(self, db, auth) -> None:
        self.auth.require_content_access(auth)
        enabled = bool(self.settings.qianchuan_enabled and self.settings.qianchuan_plan_enabled)
        if not enabled:
            raise HTTPException(404, "千川计划功能未启用")
        if not self.settings.qianchuan_plan_write_enabled:
            raise HTTPException(409, "千川计划写入未启用")
        bind = db.get_bind()
        if bind is None or not inspect(bind).has_table(Plan.__tablename__):
            raise HTTPException(503, "千川计划旁路表尚未完成显式迁移")

    @staticmethod
    def _platform_error(exc: PlatformError) -> HTTPException:
        status = 409 if exc.code in {"account_forbidden", "reauthorization_required"} else 502
        return HTTPException(status, exc.message)

    @staticmethod
    def _owned_draft(db, auth, draft_id: str) -> Plan:
        row = db.query(Plan).filter_by(
            id=draft_id, tenant_id=auth.tenant_id, created_by=auth.user_id).first()
        if not row:
            raise HTTPException(404, "未找到投放计划草稿")
        if row.local_status != "draft" or row.platform_ad_id:
            raise HTTPException(409, "已提交的计划不能按草稿覆盖")
        return row

    def create_draft(self, db, auth, payload) -> dict:
        self._require_write_gate(db, auth)
        draft_id = str(payload.draft_id) if payload.draft_id else None
        if draft_id:
            self._owned_draft(db, auth, draft_id)
        binding, connection, upload, _ = self.access.require_upload_chain(
            db, auth, str(payload.upload_id), str(payload.binding_id), payload.video_asset_id)
        if (not connection.access_token_expires_at
                or connection.access_token_expires_at <= datetime.utcnow() + timedelta(minutes=5)):
            raise HTTPException(409, "千川授权即将过期，请先同步账户或重新授权")
        credential_version = connection.credential_version
        material_id = upload.material_id
        try:
            token = self.auth.decrypt(connection.access_token_ciphertext)
            aweme = self.client.authorized_aweme(token, binding.advertiser_id, payload.aweme_id)
            if not aweme:
                raise HTTPException(422, "所选抖音号不在当前账号的授权范围内")
            if aweme["has_video_permission"] is not True:
                raise HTTPException(422, "所选抖音号没有短视频带货权限")
            product = self.client.available_product(
                token, binding.advertiser_id, payload.aweme_id, payload.product_id)
        except HTTPException:
            raise
        except PlatformError as exc:
            raise self._platform_error(exc) from None
        if not product:
            raise HTTPException(422, "所选商品不在当前账号和抖音号的可投范围内")

        # Recheck every mutable link after the provider reads and before persistence.
        db.expire_all()
        current_binding, current_connection, current_upload, _ = self.access.require_upload_chain(
            db, auth, str(payload.upload_id), str(payload.binding_id), payload.video_asset_id)
        if (current_binding.advertiser_id != binding.advertiser_id
                or current_connection.credential_version != credential_version
                or current_upload.material_id != material_id):
            raise HTTPException(409, "授权或素材在校验期间发生变化，请重新选择")

        row = self._owned_draft(db, auth, draft_id) if draft_id else None
        if row is None:
            row = Plan(id=str(uuid.uuid4()), tenant_id=auth.tenant_id, created_by=auth.user_id,
                       created_at=datetime.utcnow())
            db.add(row)

        row.connection_id = current_connection.id
        row.binding_id = current_binding.id
        row.advertiser_id = current_binding.advertiser_id
        row.credential_version = current_connection.credential_version
        row.video_asset_id = payload.video_asset_id
        row.upload_id = current_upload.id
        row.material_id = current_upload.material_id
        row.product_id = payload.product_id
        row.product_name = product["name"]
        row.aweme_id = payload.aweme_id
        row.aweme_name = aweme["name"]
        row.name = payload.name
        row.marketing_goal = payload.marketing_goal
        row.budget_minor = payload.budget_minor
        row.roi_goal = payload.roi_goal
        row.local_status = "draft"
        row.request_snapshot = {
            "schema_version": 1,
            "advertiser_id": row.advertiser_id,
            "material_id": row.material_id,
            "product_id": row.product_id,
            "aweme_id": row.aweme_id,
            "marketing_goal": row.marketing_goal,
            "budget_minor": row.budget_minor,
            "roi_goal": str(row.roi_goal),
        }
        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return self.view(row)
