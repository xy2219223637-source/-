from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import case, update

from app.auth import AuthContext
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (QianchuanAccountBinding as Binding, QianchuanConnection as Connection,
                        QianchuanOAuthSession as OAuthSession, Tenant, TenantMember, TenantRole, User, VideoAsset)
from .client import PlatformError, QianchuanClient, identifier
from .logging import configure_logging
from .cache import ReportCache

CALLBACK_PATH = "/api/qianchuan/oauth/callback"
LOCAL_ORIGINS = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (5173, 8080)}
AUTH_HOSTS = {"ad.oceanengine.com", "open.oceanengine.com", "qianchuan.jinritemai.com"}
logger = logging.getLogger(__name__)
ERROR_MESSAGES = {
    "cancelled": "已取消授权，可以重新发起",
    "expired": "授权会话已过期，请重新发起",
    "superseded": "已有更新的授权操作，请查看最新会话",
    "exchange_uncertain": "授权结果不确定，请重新授权",
    "exchange_failed": "获取授权失败，请检查应用配置和权限后重新授权",
    "owner_inactive": "发起用户的企业权限已失效",
    "interrupted": "授权期间服务中断，请重新授权",
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        return not any(term in message for term in (
            CALLBACK_PATH, "access_token", "access-token", "refresh_token", "auth_code",
            '"secret"', "'secret'"))


def install_log_filters() -> None:
    configure_logging()
    # Filter on the emitting loggers: parent logger filters are not inherited.
    for name in ("uvicorn.access", "httpx", "httpcore.http11", "httpcore.http2", "httpcore.connection"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, SensitiveLogFilter) for f in logger.filters):
            logger.addFilter(SensitiveLogFilter())


class QianchuanService:
    def __init__(self, settings: Settings, client: QianchuanClient, session_factory=SessionLocal):
        self.settings, self.client, self.session_factory = settings, client, session_factory
        self.report_cache = ReportCache()

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.settings.qianchuan_redirect_uri)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def local_http(self) -> bool:
        return self.settings.debug and self.origin in LOCAL_ORIGINS

    def authorization_template(self) -> tuple[str, dict[str, str]]:
        """Accept a configured official link; session state is always generated here."""
        auth = urlsplit(self.settings.qianchuan_authorization_url)
        if (auth.scheme != "https" or auth.hostname not in AUTH_HOSTS or auth.username
                or auth.password or auth.fragment or auth.port not in (None, 443)):
            raise ValueError("Invalid authorization URL")
        pairs = parse_qsl(auth.query, keep_blank_values=True, max_num_fields=10)
        params = dict(pairs)
        if (len(params) != len(pairs)
                or set(params) - {"app_id", "state", "scope", "redirect_uri", "material_auth", "rid"}
                or any(len(value) > 4096 for value in params.values())):
            raise ValueError("Invalid authorization parameters")
        for key, expected in (("app_id", self.settings.qianchuan_app_id),
                              ("redirect_uri", self.settings.qianchuan_redirect_uri)):
            if key in params and params[key] != expected:
                raise ValueError("Conflicting authorization configuration")
        if "material_auth" in params and params["material_auth"] not in {"0", "1"}:
            raise ValueError("Invalid material authorization parameter")
        if self.settings.qianchuan_scope and params.get("scope", self.settings.qianchuan_scope) != self.settings.qianchuan_scope:
            raise ValueError("Conflicting scope configuration")
        params.pop("state", None)
        return urlunsplit((auth.scheme, auth.netloc, auth.path, "", "")), params

    def configuration(self) -> dict:
        if not self.settings.qianchuan_enabled:
            return {"status": "disabled", "missing": []}
        required = ("qianchuan_app_id", "qianchuan_app_secret", "qianchuan_authorization_url",
                    "qianchuan_redirect_uri", "qianchuan_token_encryption_key")
        missing = [name.upper() for name in required if not getattr(self.settings, name).strip()]
        try:
            identifier(self.settings.qianchuan_app_id)
        except PlatformError:
            missing.append("QIANCHUAN_APP_ID")
        try:
            callback = urlsplit(self.settings.qianchuan_redirect_uri)
            callback.port  # Validate a malformed/out-of-range port without exposing the URL in errors.
            valid_scheme = callback.scheme == "https" or self.local_http
            if (not valid_scheme or not callback.hostname or callback.username or callback.password
                    or callback.path != CALLBACK_PATH or callback.query or callback.fragment):
                missing.append("QIANCHUAN_REDIRECT_URI")
        except ValueError:
            missing.append("QIANCHUAN_REDIRECT_URI")
        try:
            self.authorization_template()
        except ValueError:
            missing.append("QIANCHUAN_AUTHORIZATION_URL")
        try:
            Fernet(self.settings.qianchuan_token_encryption_key.encode())
        except (ValueError, TypeError):
            missing.append("QIANCHUAN_TOKEN_ENCRYPTION_KEY")
        return {"status": "invalid_configuration" if missing else "ready", "missing": sorted(set(missing))}

    def require_ready(self) -> None:
        if self.configuration()["status"] != "ready":
            raise HTTPException(503, "千川授权功能未启用或配置不完整，请联系管理员")

    def cookie_name(self, session_id: str) -> str:
        return ("" if self.local_http else "__Host-") + f"qc_oauth_{session_id}"

    def encrypt(self, value: str) -> str:
        return "v1:" + Fernet(self.settings.qianchuan_token_encryption_key.encode()).encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            if not value.startswith("v1:"):
                raise ValueError
            return Fernet(self.settings.qianchuan_token_encryption_key.encode()).decrypt(value[3:].encode()).decode()
        except (InvalidToken, ValueError, UnicodeError):
            raise PlatformError("reauthorization_required", "授权凭据无法解密，请重新授权") from None

    def token_values(self, data: dict) -> dict:
        try:
            for key in ("access_token", "refresh_token"):
                if not isinstance(data[key], str) or not data[key] or len(data[key]) > 8192:
                    raise ValueError
            for key in ("expires_in", "refresh_token_expires_in"):
                if type(data[key]) is not int or not 0 < data[key] <= 10 * 365 * 86400:
                    raise ValueError
            now = datetime.utcnow()
            return {
                "access_token_ciphertext": self.encrypt(data["access_token"]),
                "refresh_token_ciphertext": self.encrypt(data["refresh_token"]),
                "access_token_expires_at": now + timedelta(seconds=data["expires_in"]),
                "refresh_token_expires_at": now + timedelta(seconds=data["refresh_token_expires_in"]),
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            raise PlatformError("invalid_response", "平台 Token 或有效期字段无效，请重新授权") from None

    @staticmethod
    def member_active(db, user_id: str, tenant_id: str) -> bool:
        member = db.query(TenantMember).filter_by(user_id=user_id, tenant_id=tenant_id, status="active").first()
        if not member or not db.query(User).filter_by(id=user_id, status="active").first() \
                or not db.query(Tenant).filter_by(id=tenant_id, status="active").first():
            return False
        if member.role == "owner":
            return True
        role = db.query(TenantRole).filter_by(id=member.role_id, tenant_id=tenant_id, status="active").first() \
            if member.role_id else None
        return bool(role and "final_videos.access" in (role.permissions or []))

    @staticmethod
    def require_content_access(auth: AuthContext) -> None:
        if auth.role != "owner" and "final_videos.access" not in auth.permissions:
            raise HTTPException(403, "当前角色没有成片库权限")

    @staticmethod
    def connection(db, connection_id: str, tenant_id: str, user_id: str) -> Connection:
        row = db.query(Connection).filter_by(id=connection_id, tenant_id=tenant_id, created_by=user_id).first()
        if not row:
            raise HTTPException(404, "未找到千川连接")
        return row

    def create_session(self, db, auth: AuthContext, video_asset_id: int | None, connection_id: str | None) -> dict:
        self.require_content_access(auth)
        self.require_ready()
        if video_asset_id is not None:
            asset = db.query(VideoAsset).filter_by(id=video_asset_id, tenant_id=auth.tenant_id,
                                                  asset_kind="final_output", excluded=False).first()
            if not asset:
                raise HTTPException(404, "未找到本企业成片")
        session_id, state, nonce = str(uuid.uuid4()), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        if connection_id:
            conn = self.connection(db, connection_id, auth.tenant_id, auth.user_id)
            if conn.status == "deleted":
                raise HTTPException(404, "此授权连接已删除，请添加新的授权")
            db.query(OAuthSession).filter(OAuthSession.connection_id == conn.id,
                                          OAuthSession.tenant_id == auth.tenant_id,
                                          OAuthSession.status.in_(["pending", "processing"])).update(
                {"status": "failed", "error_code": "superseded"}, synchronize_session=False)
            changed = db.execute(update(Connection).where(Connection.id == conn.id, Connection.tenant_id == auth.tenant_id,
                       Connection.created_by == auth.user_id,
                       Connection.status != "deleted")
                       .values(current_session_id=session_id,
                               status=case((Connection.status == "disabled", "authorizing"), else_=Connection.status))).rowcount
            if not changed:
                db.rollback()
                raise HTTPException(404, "此授权连接已删除，请添加新的授权")
        else:
            # One application user owns one reusable Qianchuan authorization
            # connection. Starting another authorization must rotate that
            # connection instead of accumulating visually identical links.
            conn = db.query(Connection).filter(
                Connection.tenant_id == auth.tenant_id,
                Connection.created_by == auth.user_id,
                Connection.status != "deleted",
            ).order_by(Connection.created_at.desc()).first()
            if conn:
                db.query(OAuthSession).filter(OAuthSession.connection_id == conn.id,
                                              OAuthSession.tenant_id == auth.tenant_id,
                                              OAuthSession.status.in_(["pending", "processing"])).update(
                    {"status": "failed", "error_code": "superseded"}, synchronize_session=False)
                changed = db.execute(update(Connection).where(
                    Connection.id == conn.id, Connection.tenant_id == auth.tenant_id,
                    Connection.created_by == auth.user_id, Connection.status != "deleted"
                ).values(current_session_id=session_id,
                         status=case((Connection.status == "disabled", "authorizing"),
                                     else_=Connection.status))).rowcount
                if not changed:
                    db.rollback()
                    raise HTTPException(409, "授权连接状态已变化，请重试")
            else:
                conn = Connection(id=str(uuid.uuid4()), tenant_id=auth.tenant_id, created_by=auth.user_id,
                                  current_session_id=session_id, credential_version=1)
                db.add(conn)
                db.flush()
        db.add(OAuthSession(id=session_id, tenant_id=auth.tenant_id, user_id=auth.user_id, connection_id=conn.id,
                            state_hash=digest(state), browser_nonce_hash=digest(nonce), video_asset_id=video_asset_id,
                            expires_at=datetime.utcnow() + timedelta(minutes=10)))
        db.commit()
        authorization_base, params = self.authorization_template()
        params.update({"app_id": self.settings.qianchuan_app_id, "redirect_uri": self.settings.qianchuan_redirect_uri,
                       "state": state})
        if self.settings.qianchuan_scope:
            params["scope"] = self.settings.qianchuan_scope
        logger.info("qianchuan action=authorize_started tenant=%s actor=%s session=%s", auth.tenant_id, auth.user_id, session_id)
        return {"session_id": session_id, "nonce": nonce,
                "authorization_url": authorization_base + "?" + urlencode(params)}

    def callback(self, db, state: str, cookies: dict, auth_code: str | None, error: str | None) -> str:
        self.require_ready()
        row = db.query(OAuthSession).filter_by(state_hash=digest(state)).first()
        if not row or row.status != "pending":
            raise HTTPException(400, "授权会话无效或已经使用，请重新发起")
        nonce = cookies.get(self.cookie_name(row.id), "")
        if not nonce or not hmac.compare_digest(digest(nonce), row.browser_nonce_hash):
            raise HTTPException(400, "请在发起授权的同一浏览器完成回调")
        if row.expires_at <= datetime.utcnow():
            row.status, row.error_code = "failed", "expired"
            db.commit()
            raise HTTPException(400, ERROR_MESSAGES["expired"])
        if not self.member_active(db, row.user_id, row.tenant_id):
            raise HTTPException(400, ERROR_MESSAGES["owner_inactive"])
        session_id, connection_id, tenant_id, user_id = row.id, row.connection_id, row.tenant_id, row.user_id
        result = db.execute(update(OAuthSession).where(
            OAuthSession.id == session_id, OAuthSession.status == "pending",
            OAuthSession.expires_at > datetime.utcnow()
        ).values(status="processing", consumed_at=datetime.utcnow()))
        db.commit()
        if result.rowcount != 1:
            raise HTTPException(400, "授权会话已经使用")
        failure = ""
        values = None
        if error:
            failure = "cancelled"
        else:
            try:
                values = self.token_values(self.client.exchange(auth_code))
            except PlatformError as exc:
                failure = "exchange_uncertain" if exc.code in {"transport_uncertain", "http_error", "invalid_response"} else "exchange_failed"
        if not self.member_active(db, user_id, tenant_id):
            failure = "owner_inactive"
        # New authorization, disable and refresh races cannot overwrite a newer session.
        conn = db.query(Connection).filter_by(id=connection_id, tenant_id=tenant_id, created_by=user_id).first()
        if not conn or conn.current_session_id != session_id or conn.status == "disabled":
            failure = "superseded"
        if failure:
            db.execute(update(OAuthSession).where(OAuthSession.id == session_id).values(status="failed", error_code=failure))
            if failure in {"exchange_uncertain", "owner_inactive"}:
                db.execute(update(Connection).where(Connection.id == connection_id,
                           Connection.current_session_id == session_id, Connection.status != "disabled")
                           .values(status="reauthorization_required", error_code=failure))
            db.commit()
            logger.info("qianchuan action=authorize_failed tenant=%s session=%s reason=%s", tenant_id, session_id, failure)
            return session_id
        result = db.execute(update(Connection).where(Connection.id == connection_id, Connection.tenant_id == tenant_id,
                            Connection.created_by == user_id,
                            Connection.current_session_id == session_id, Connection.status != "disabled")
                            .values(**values, status="sync_pending", error_code="", error_message="",
                                    credential_version=Connection.credential_version + 1))
        if result.rowcount != 1:
            db.rollback()
            db.execute(update(OAuthSession).where(OAuthSession.id == session_id).values(status="failed", error_code="superseded"))
        else:
            db.query(Binding).filter_by(connection_id=connection_id, tenant_id=tenant_id).update({"is_valid": False})
            db.execute(update(OAuthSession).where(OAuthSession.id == session_id)
                       .values(status="completed", error_code=""))
        db.commit()
        logger.info("qianchuan action=authorize_finished tenant=%s session=%s", tenant_id, session_id)
        return session_id

    def session_result(self, db, auth: AuthContext, session_id: str) -> dict:
        self.require_content_access(auth)
        row = db.query(OAuthSession).filter_by(id=session_id, user_id=auth.user_id, tenant_id=auth.tenant_id).first()
        if not row:
            raise HTTPException(404, "未找到授权会话")
        if row.status == "pending" and row.expires_at <= datetime.utcnow():
            row.status, row.error_code = "failed", "expired"
            db.commit()
        conn = self.connection(db, row.connection_id, auth.tenant_id, auth.user_id)
        return {"session_id": row.id, "status": row.status, "error_code": row.error_code,
                "message": ERROR_MESSAGES.get(row.error_code, ""), "connection_id": conn.id,
                "connection_status": conn.status, "connection_error": conn.error_message,
                "return_path": f"/final-videos/{row.video_asset_id}" if row.video_asset_id else "/final-videos"}

    def accounts(self, db, auth: AuthContext) -> dict:
        self.require_content_access(auth)
        config = self.configuration()
        connections = db.query(Connection).filter(Connection.tenant_id == auth.tenant_id,
            Connection.created_by == auth.user_id,
            Connection.status != "deleted").order_by(Connection.created_at.desc()).all()
        result = []
        if connections:
            # Legacy versions could create another connection on every OAuth
            # round-trip.  Treat all of them as one personal authorization in
            # the API without deleting audit/history rows. The newest row is
            # the reusable connection for future authorization operations.
            primary = connections[0]
            connection_ids = [row.id for row in connections if row.status == "active"]
            bindings = db.query(Binding).filter(
                Binding.tenant_id == auth.tenant_id,
                Binding.connection_id.in_(connection_ids),
                Binding.is_valid.is_(True),
            ).all() if connection_ids else []
            priority = {"ADVERTISER": 4, "SHOP": 3, "AGENT": 2}
            unique_bindings = {}
            for binding in bindings:
                current = unique_bindings.get(binding.advertiser_id)
                rank = (priority.get(binding.source_account_type, 1),
                        bool(binding.advertiser_name), bool(binding.source_account_name))
                current_rank = ((priority.get(current.source_account_type, 1),
                                 bool(current.advertiser_name), bool(current.source_account_name))) \
                    if current else (-1, False, False)
                if current is None or rank > current_rank:
                    unique_bindings[binding.advertiser_id] = binding
            warnings = sorted({warning for row in connections for warning in (row.warnings or [])})
            result.append({
                "id": primary.id, "status": primary.status, "error": primary.error_message,
                "warnings": warnings, "last_synced_at": primary.last_synced_at,
                "accounts": [{"id": b.id, "advertiser_id": b.advertiser_id, "advertiser_name": b.advertiser_name,
                              "source_account_name": b.source_account_name, "source_account_type": b.source_account_type,
                              "source_account_id": b.source_account_id} for b in unique_bindings.values()]
                            if config["status"] == "ready" else [],
            })
        return {"configuration": config, "can_manage": True, "connections": result}

    def request_sync(self, db, auth: AuthContext, connection_id: str) -> None:
        self.require_content_access(auth)
        self.require_ready()
        conn = self.connection(db, connection_id, auth.tenant_id, auth.user_id)
        if conn.status not in {"active", "sync_failed", "sync_pending", "syncing"}:
            raise HTTPException(409, "请先重新授权")
        # Repeated clicks while a sync is running do not reset its claim.
        db.execute(update(Connection).where(Connection.id == conn.id, Connection.tenant_id == auth.tenant_id,
                   Connection.created_by == auth.user_id,
                   Connection.status.in_(["active", "sync_failed"])).values(status="sync_pending"))
        db.commit()

    def disable(self, db, auth: AuthContext, connection_id: str, *, remove: bool = False) -> None:
        self.require_content_access(auth)
        conn = self.connection(db, connection_id, auth.tenant_id, auth.user_id)
        # Keep the record for upload history; deleted connections cannot be reactivated by stale requests.
        db.execute(update(Connection).where(Connection.id == conn.id, Connection.tenant_id == auth.tenant_id,
                   Connection.created_by == auth.user_id,
                   Connection.status != "deleted")
                   .values(status="deleted" if remove else "disabled", current_session_id="", access_token_ciphertext="",
                           refresh_token_ciphertext="", credential_version=Connection.credential_version + 1))
        db.query(Binding).filter_by(connection_id=conn.id, tenant_id=auth.tenant_id).update({"is_valid": False})
        db.query(OAuthSession).filter(OAuthSession.connection_id == conn.id, OAuthSession.tenant_id == auth.tenant_id,
                                      OAuthSession.status.in_(["pending", "processing"])).update(
                                          {"status": "failed", "error_code": "superseded"})
        db.commit()
        logger.info("qianchuan action=%s tenant=%s actor=%s connection=%s", "deleted" if remove else "disabled",
                    auth.tenant_id, auth.user_id, connection_id)

    def sync_connection(self, connection_id: str) -> None:
        with self.session_factory() as db:
            conn = db.get(Connection, connection_id)
            if not conn:
                return
            tenant_id, version = conn.tenant_id, conn.credential_version
            claimed = db.execute(update(Connection).where(Connection.id == conn.id, Connection.status == "sync_pending",
                                 Connection.credential_version == version).values(status="syncing")).rowcount
            db.commit()
            if not claimed:
                return

            def check():
                db.expire_all()
                current = db.get(Connection, connection_id)
                if not current or current.status != "syncing" or current.credential_version != version:
                    raise PlatformError("superseded", "账户同步已被新的授权或停用操作取代")
                if not self.member_active(db, current.created_by, tenant_id):
                    raise PlatformError("reauthorization_required", "授权用户或企业状态无效")
                # Do not keep database transactions open across network requests.
                db.commit()

            try:
                check()
                conn = db.get(Connection, connection_id)
                expiry, refresh_expiry = conn.access_token_expires_at, conn.refresh_token_expires_at
                access_cipher, refresh_cipher = conn.access_token_ciphertext, conn.refresh_token_ciphertext
                db.commit()
                if not expiry or not refresh_expiry or refresh_expiry <= datetime.utcnow():
                    raise PlatformError("reauthorization_required", "授权已过期，请重新授权")
                if expiry <= datetime.utcnow() + timedelta(minutes=5):
                    check()
                    try:
                        values = self.token_values(self.client.refresh(self.decrypt(refresh_cipher)))
                    except PlatformError:
                        raise PlatformError("reauthorization_required", "刷新授权失败或结果不确定，请重新授权") from None
                    changed = db.execute(update(Connection).where(Connection.id == connection_id,
                        Connection.credential_version == version, Connection.status == "syncing")
                        .values(**values, credential_version=version + 1)).rowcount
                    db.commit()
                    if not changed:
                        return
                    version += 1
                    access_cipher = values["access_token_ciphertext"]
                bindings, snapshot, warnings = self.client.resolve_accounts(self.decrypt(access_cipher), check)
                check()
                changed = db.execute(update(Connection).where(Connection.id == connection_id,
                    Connection.tenant_id == tenant_id, Connection.credential_version == version, Connection.status == "syncing")
                    .values(status="active", account_snapshot=snapshot, warnings=warnings, last_synced_at=datetime.utcnow(),
                            error_code="", error_message="", provider_request_id="")).rowcount
                if changed:
                    db.query(Binding).filter_by(connection_id=connection_id, tenant_id=tenant_id).update({"is_valid": False})
                    for data in bindings:
                        duplicates = db.query(Binding).filter_by(
                            connection_id=connection_id, tenant_id=tenant_id,
                            advertiser_id=data["advertiser_id"]).order_by(Binding.id).all()
                        row = next((item for item in duplicates
                                    if item.source_account_id == data["source_account_id"]),
                                   duplicates[0] if duplicates else None)
                        if row:
                            for key, value in data.items():
                                setattr(row, key, value)
                            row.is_valid = True
                            for duplicate in duplicates:
                                if duplicate.id != row.id:
                                    duplicate.is_valid = False
                        else:
                            db.add(Binding(id=str(uuid.uuid4()), tenant_id=tenant_id, connection_id=connection_id, **data))
                db.commit()
                logger.info("qianchuan action=accounts_synced tenant=%s connection=%s count=%s", tenant_id, connection_id,
                            len(bindings) if changed else 0)
            except Exception as exc:
                db.rollback()
                error = exc if isinstance(exc, PlatformError) else PlatformError("internal_error", "账户同步失败，请稍后重试")
                db.execute(update(Connection).where(Connection.id == connection_id, Connection.credential_version == version,
                           Connection.status == "syncing").values(
                    status="reauthorization_required" if error.code == "reauthorization_required" else "sync_failed",
                    error_code=error.code, error_message=error.message, provider_request_id=error.request_id))
                db.commit()
                logger.info("qianchuan action=sync_failed tenant=%s connection=%s reason=%s", tenant_id, connection_id, error.code)

    def recover(self) -> None:
        with self.session_factory() as db:
            # Single-process deployment only. A code exchange cannot be replayed after a crash.
            db.query(Connection).filter_by(status="syncing").update({"status": "sync_pending"})
            for row in db.query(OAuthSession).filter_by(status="processing").all():
                row.status, row.error_code = "failed", "interrupted"
                db.execute(update(Connection).where(Connection.id == row.connection_id,
                    Connection.current_session_id == row.id, Connection.status != "disabled")
                    .values(status="reauthorization_required", error_code="interrupted",
                            error_message=ERROR_MESSAGES["interrupted"]))
            db.commit()


@lru_cache
def get_qianchuan_service() -> QianchuanService:
    settings = get_settings()
    return QianchuanService(settings, QianchuanClient(settings))
