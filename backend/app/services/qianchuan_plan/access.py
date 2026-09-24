from __future__ import annotations

from fastapi import HTTPException

from app.models import (QianchuanAccountBinding as Binding, QianchuanConnection as Connection,
                        QianchuanDeliveryPlan as Plan, QianchuanUpload as Upload, VideoAsset)


class QianchuanPlanAccess:
    """Central owner-chain checks for the sidecar plan domain."""

    def __init__(self, auth_service):
        self.auth_service = auth_service

    def require_binding(self, db, auth, binding_id: str):
        self.auth_service.require_content_access(auth)
        row = db.query(Binding, Connection).join(Connection, Binding.connection_id == Connection.id).filter(
            Binding.id == binding_id,
            Binding.tenant_id == auth.tenant_id,
            Connection.tenant_id == auth.tenant_id,
            Connection.created_by == auth.user_id,
        ).first()
        if not row:
            raise HTTPException(404, "未找到千川账户")
        binding, connection = row
        if not binding.is_valid or connection.status != "active":
            raise HTTPException(409, "目标账户不可用，请先同步账户或重新授权")
        return binding, connection

    def require_upload_chain(self, db, auth, upload_id: str, binding_id: str, video_asset_id: int):
        binding, connection = self.require_binding(db, auth, binding_id)
        upload = db.query(Upload).filter_by(
            id=upload_id, tenant_id=auth.tenant_id, created_by=auth.user_id,
            binding_id=binding.id, advertiser_id=binding.advertiser_id,
            video_asset_id=video_asset_id,
        ).first()
        asset = db.query(VideoAsset).filter_by(
            id=video_asset_id, tenant_id=auth.tenant_id,
            asset_kind="final_output", excluded=False,
        ).first()
        if not upload or not asset:
            raise HTTPException(404, "未找到本人账号下的成片上传记录")
        if upload.status != "succeeded" or not upload.material_id:
            raise HTTPException(409, "成片尚未成功上传并完成平台素材核验")
        return binding, connection, upload, asset

    def require_plan(self, db, auth, plan_id: str):
        self.auth_service.require_content_access(auth)
        plan = db.query(Plan).filter_by(
            id=plan_id, tenant_id=auth.tenant_id, created_by=auth.user_id).first()
        if not plan:
            raise HTTPException(404, "未找到投放计划")
        return plan

