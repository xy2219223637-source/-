from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.models import QianchuanCapabilityProbe as Probe
from app.schemas.qianchuan_plans import QianchuanCapabilityItem, QianchuanPlanCapabilities
from app.services.qianchuan.client import PlatformError
from .access import QianchuanPlanAccess
from .client import QianchuanPlanClient


CREATE_CAPABILITIES = {"authorized_awemes", "available_products", "plan_list:VIDEO_PROM_GOODS"}


class QianchuanCatalogService:
    def __init__(self, auth_service, plan_client: QianchuanPlanClient | None = None):
        self.auth_service = auth_service
        self.settings = auth_service.settings
        self.access = QianchuanPlanAccess(auth_service)
        self.client = plan_client or QianchuanPlanClient(auth_service.client)

    def gates(self) -> tuple[bool, bool, bool]:
        enabled = bool(self.settings.qianchuan_enabled and self.settings.qianchuan_plan_enabled)
        write_enabled = bool(enabled and self.settings.qianchuan_plan_write_enabled)
        worker_enabled = bool(write_enabled and self.settings.qianchuan_plan_worker_enabled)
        return enabled, write_enabled, worker_enabled

    def capabilities(self, db, auth, binding_id: str | None = None) -> QianchuanPlanCapabilities:
        self.auth_service.require_content_access(auth)
        enabled, write_enabled, worker_enabled = self.gates()
        if binding_id is None:
            return QianchuanPlanCapabilities(
                enabled=enabled, write_enabled=write_enabled, worker_enabled=worker_enabled)

        binding, connection = self.access.require_binding(db, auth, binding_id)
        if not enabled:
            return QianchuanPlanCapabilities(
                enabled=False, write_enabled=False, worker_enabled=False,
                binding_id=binding.id, credential_version=connection.credential_version)

        rows = db.query(Probe).filter(
            Probe.tenant_id == auth.tenant_id,
            Probe.created_by == auth.user_id,
            Probe.connection_id == connection.id,
            Probe.binding_id == binding.id,
            Probe.advertiser_id == binding.advertiser_id,
            Probe.credential_version == connection.credential_version,
            Probe.expires_at > datetime.utcnow(),
        ).order_by(Probe.probed_at.desc()).all()
        latest = {}
        for row in rows:
            latest.setdefault(row.capability, row)
        items = [QianchuanCapabilityItem(
            name=name, conclusion=row.conclusion, probed_at=row.probed_at, expires_at=row.expires_at)
            for name, row in sorted(latest.items())]
        verified = {item.name for item in items if item.conclusion == "verified"}
        return QianchuanPlanCapabilities(
            enabled=enabled, write_enabled=write_enabled, worker_enabled=worker_enabled,
            binding_id=binding.id, credential_version=connection.credential_version,
            can_create=write_enabled and CREATE_CAPABILITIES <= verified,
            capabilities=items,
        )

    def _read_context(self, db, auth, binding_id: str):
        self.auth_service.require_content_access(auth)
        enabled, _, _ = self.gates()
        if not enabled:
            raise HTTPException(404, "千川计划功能未启用")
        binding, connection = self.access.require_binding(db, auth, binding_id)
        if (not connection.access_token_expires_at
                or connection.access_token_expires_at <= datetime.utcnow() + timedelta(minutes=5)):
            raise HTTPException(409, "千川授权即将过期，请先同步账户或重新授权")
        try:
            token = self.auth_service.decrypt(connection.access_token_ciphertext)
        except PlatformError as exc:
            raise HTTPException(409, exc.message) from None
        return binding, token

    @staticmethod
    def _platform_call(callback):
        try:
            return callback()
        except PlatformError as exc:
            status = 409 if exc.code in {"account_forbidden", "reauthorization_required"} else 502
            raise HTTPException(status, exc.message) from None

    def awemes(self, db, auth, binding_id: str, *, page: int, page_size: int, keyword: str = ""):
        binding, token = self._read_context(db, auth, binding_id)
        return self._platform_call(lambda: self.client.authorized_awemes(
            token, binding.advertiser_id, page=page, page_size=page_size, keyword=keyword))

    def products(
        self, db, auth, binding_id: str, *, aweme_id: str | None = None,
        keyword: str = "", cursor: int | None = None, page_size: int = 20,
    ):
        binding, token = self._read_context(db, auth, binding_id)
        return self._platform_call(lambda: self.client.available_products(
            token, binding.advertiser_id, aweme_id=aweme_id, keyword=keyword,
            cursor=cursor, page_size=page_size))
