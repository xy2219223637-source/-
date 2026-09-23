from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.models import (QianchuanUpload as Upload, QianchuanAccountBinding as Binding,
                        QianchuanConnection as Connection, VideoAsset)
from app.services.storage import get_storage, object_key
from .client import PlatformError, identifier

logger = logging.getLogger(__name__)
MAX_BYTES = 50 * 1024 * 1024  # First release product limit, not a platform limit.
ACTIVE = {"queued", "running", "uploaded", "unknown"}


def load_source(url: str) -> Path:
    storage = get_storage()
    key = object_key(url)
    if not key:
        raise PlatformError("source_invalid", "成片存储地址无效")
    if int(storage.provider.bucket.head_object(key).content_length) > MAX_BYTES:
        raise PlatformError("source_too_large", "首版支持不超过 50 MB 的 MP4 成片")
    path = storage.local_path(url)
    if not path:
        raise PlatformError("source_missing", "无法读取成片文件")
    return path


class UploadService:
    def __init__(self, auth_service, source_loader=load_source):
        self.auth = auth_service
        self.client = auth_service.client
        self.sessions = auth_service.session_factory
        self.source_loader = source_loader

    @staticmethod
    def view(row):
        return {key: getattr(row, key) for key in (
            "id", "video_asset_id", "binding_id", "advertiser_id", "advertiser_name", "filename", "is_aigc",
            "status", "phase", "material_id", "video_id", "error_message", "created_at", "updated_at"
        )} | {"auto_pending": row.status == "queued" or row.status == "running" or
              (row.status in {"uploaded", "unknown"} and row.next_attempt_at is not None),
              "can_retry": row.status == "failed" and row.attempts < 3,
              "can_verify": row.status in {"uploaded", "unknown"} and row.next_attempt_at is None}

    @staticmethod
    def asset(db, tenant_id, video_id):
        row = db.query(VideoAsset).filter_by(id=video_id, tenant_id=tenant_id,
                                            asset_kind="final_output", excluded=False).first()
        if not row:
            raise HTTPException(404, "未找到本企业成片")
        return row

    @staticmethod
    def binding(db, tenant_id, binding_id, owner_user_id=None):
        b = db.query(Binding).filter_by(id=binding_id, tenant_id=tenant_id).first()
        c = db.query(Connection).filter_by(id=b.connection_id, tenant_id=tenant_id).first() if b else None
        if not b or not c or (owner_user_id is not None and c.created_by != owner_user_id):
            raise HTTPException(404, "未找到千川账户")
        if not b.is_valid or c.status != "active":
            raise HTTPException(409, "目标账户不可用，请先同步账户或重新授权")
        return b, c

    @staticmethod
    def owned_uploads(db, tenant_id, user_id):
        return db.query(Upload).join(Binding, Upload.binding_id == Binding.id).join(
            Connection, Binding.connection_id == Connection.id).filter(
            Upload.tenant_id == tenant_id, Upload.created_by == user_id,
            Binding.tenant_id == tenant_id, Connection.tenant_id == tenant_id,
            Connection.created_by == user_id)

    def create(self, db, auth, video_id, binding_id, filename, is_aigc):
        self.auth.require_content_access(auth)
        self.auth.require_ready()
        if auth.role not in {"owner", "member"}:
            raise HTTPException(403, "当前角色不能上传素材")
        a = self.asset(db, auth.tenant_id, video_id)
        b, _ = self.binding(db, auth.tenant_id, binding_id, auth.user_id)
        if a.status not in {"uploaded", "analyzed", "ready", "completed"} or not re.fullmatch(r"[a-fA-F0-9]{64}", a.content_hash):
            raise HTTPException(409, "成片尚未就绪或缺少内容校验值")
        if not a.filename.lower().endswith(".mp4") or not a.object_url.startswith("/api/storage/objects/"):
            raise HTTPException(400, "首版仅支持存储中的 MP4 成片")
        filename = (filename or a.filename).strip()
        if not filename or len(filename) > 255 or re.search(r'[\\/\x00-\x1f\x7f]', filename):
            raise HTTPException(400, "素材名称应为 1 至 255 个字符，不能包含路径或控制字符")
        key = dict(tenant_id=auth.tenant_id, video_asset_id=a.id, source_content_hash=a.content_hash,
                   advertiser_id=b.advertiser_id, created_by=auth.user_id, binding_id=b.id)
        existing = db.query(Upload).filter_by(**key).first()
        if existing:
            return self.view(existing)
        row = Upload(id=str(uuid.uuid4()), **key,
                     advertiser_name=b.advertiser_name, source_url=a.object_url, filename=filename,
                     is_aigc=is_aigc, status="queued")
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(Upload).filter_by(**key).first()
            if not row:
                raise HTTPException(409, "上传任务创建冲突，请重新读取") from None
        return self.view(row)

    def list(self, db, auth, video_id):
        self.auth.require_content_access(auth)
        self.asset(db, auth.tenant_id, video_id)
        return [self.view(row) for row in self.owned_uploads(db, auth.tenant_id, auth.user_id).filter(
            Upload.video_asset_id == video_id).order_by(Upload.created_at.desc()).all()]

    def retry(self, db, auth, upload_id):
        self.auth.require_content_access(auth)
        if auth.role not in {"owner", "member"}:
            raise HTTPException(403, "当前角色不能重试")
        row = self.owned_uploads(db, auth.tenant_id, auth.user_id).filter(Upload.id == upload_id).first()
        if not row:
            raise HTTPException(404, "未找到上传记录")
        self.auth.require_ready()
        self.binding(db, auth.tenant_id, row.binding_id, auth.user_id)
        if row.status == "failed" and row.attempts < 3:
            db.execute(update(Upload).where(Upload.id == row.id, Upload.status == "failed",
                Upload.attempts == row.attempts).values(status="queued", phase=""))
        elif row.status in {"uploaded", "unknown"}:
            # Never resend an ambiguous write. Explicit action schedules queries only.
            row.next_attempt_at, row.verify_attempts = datetime.utcnow(), 0
        elif row.status not in ACTIVE | {"succeeded"}:
            raise HTTPException(409, "已达到重试上限，请检查成片和授权")
        db.commit()
        return self.view(row)

    def access(self, upload_id, expected_version=None):
        self.auth.require_ready()
        with self.sessions() as db:
            row = db.get(Upload, upload_id)
            if not row:
                raise PlatformError("scope_changed", "上传任务已不存在")
            b, c = self.binding(db, row.tenant_id, row.binding_id)
            if b.advertiser_id != row.advertiser_id or c.created_by != row.created_by:
                raise PlatformError("scope_changed", "账户范围已变化，请核对授权")
            if not self.auth.member_active(db, row.created_by, row.tenant_id):
                raise PlatformError("owner_inactive", "发起人的企业权限已失效")
            if expected_version is not None and c.credential_version != expected_version:
                raise PlatformError("scope_changed", "授权已变化，请重新核验账户")
            if not c.access_token_expires_at or c.access_token_expires_at <= datetime.utcnow() + timedelta(minutes=5):
                # Let the existing CAS-protected sync path refresh credentials and recheck account scope.
                db.execute(update(Connection).where(Connection.id == c.id, Connection.status == "active",
                    Connection.credential_version == c.credential_version).values(status="sync_pending"))
                db.commit()
                raise PlatformError("refresh_pending", "授权即将过期，正在同步账户，请稍后重试")
            return self.auth.decrypt(c.access_token_ciphertext), c.credential_version

    def update(self, upload_id, **values):
        with self.sessions() as db:
            db.execute(update(Upload).where(Upload.id == upload_id).values(**values))
            db.commit()
        if "status" in values:
            logger.info("qianchuan upload=%s status=%s", upload_id, values["status"])

    def run_next(self):
        with self.sessions() as db:
            row = db.query(Upload).filter(or_(Upload.status == "queued",
                (Upload.status.in_(["uploaded", "unknown"]) & (Upload.next_attempt_at <= datetime.utcnow())))).order_by(
                    Upload.created_at).first()
            if not row:
                return False
            upload_id, status = row.id, row.status
        if status == "queued":
            self.send(upload_id)
        else:
            self.verify(upload_id)
        return True

    def send(self, upload_id):
        with self.sessions() as db:
            claimed = db.execute(update(Upload).where(Upload.id == upload_id, Upload.status == "queued").values(
                status="running", phase="preparing", attempts=Upload.attempts + 1, error_code="", error_message="")).rowcount
            db.commit()
            if not claimed:
                return
            row = db.get(Upload, upload_id)
            source_url, source_hash = row.source_url, row.source_content_hash
            advertiser_id, filename, is_aigc = row.advertiser_id, row.filename, row.is_aigc
        sending = False
        try:
            _, version = self.access(upload_id)
            with self.sessions() as db:
                row = db.get(Upload, upload_id)
                a = self.asset(db, row.tenant_id, row.video_asset_id)
                if a.content_hash != source_hash or a.object_url != source_url:
                    raise PlatformError("source_changed", "成片已变化，请重新选择并上传")
            path = self.source_loader(source_url)
            # Own a private copy: cache eviction or source edits cannot change the uploaded bytes.
            with tempfile.TemporaryFile() as target, open(path, "rb") as source:
                sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
                prefix = source.read(12)
                if len(prefix) < 12 or prefix[4:8] != b"ftyp":
                    raise PlatformError("source_invalid", "成片不是有效的 MP4 容器")
                source.seek(0)
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise PlatformError("source_too_large", "首版支持不超过 50 MB 的 MP4 成片")
                    sha.update(chunk); md5.update(chunk); target.write(chunk)
                if sha.hexdigest() != source_hash.lower():
                    raise PlatformError("source_changed", "成片内容校验失败，未发送到千川")
                signature = md5.hexdigest()
                token, _ = self.access(upload_id, version)
                # Confirm the target can be queried before any write. A07 permission is separate from A06.
                deadline = time.monotonic() + 30
                def check():
                    if time.monotonic() > deadline:
                        raise PlatformError("query_timeout", "上传前账户核验超时，请稍后重试")
                    self.access(upload_id, version)
                self.client.find_video(token, advertiser_id, signature, "", "", check)
                self.access(upload_id, version)
                self.update(upload_id, phase="uploading", signature=signature)
                sending = True
                target.seek(0)
                result = self.client.upload_video(token, advertiser_id, target, filename, signature, is_aigc)
                # Persist remote IDs immediately, even if the connection was disabled while sending.
                material_id = identifier(result["material_id"]) if result.get("material_id") else ""
                video_id = result.get("video_id", "")
                if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id):
                    video_id = ""
                if not material_id and not video_id:
                    raise PlatformError("invalid_response", "上传响应缺少素材标识，需查询核验")
                self.update(upload_id, status="uploaded", phase="verifying", material_id=material_id,
                            video_id=video_id, next_attempt_at=datetime.utcnow(), verify_attempts=0)
            self.verify(upload_id)
        except Exception as exc:
            error = exc if isinstance(exc, PlatformError) else PlatformError("upload_error", "上传处理异常，请核对账户和成片后重试")
            self.update(upload_id, status="unknown" if sending else "failed", phase="verifying" if sending else "preparing",
                error_code=error.code, error_message=error.message, provider_request_id=error.request_id,
                next_attempt_at=datetime.utcnow() + timedelta(seconds=5) if sending else None)

    def verify(self, upload_id):
        with self.sessions() as db:
            row = db.get(Upload, upload_id)
            if row.status not in {"uploaded", "unknown"}:
                return
            status, attempt = row.status, row.verify_attempts + 1
            advertiser_id, signature, material_id, video_id = row.advertiser_id, row.signature, row.material_id, row.video_id
            row.verify_attempts, row.next_attempt_at = attempt, None
            db.commit()
        try:
            token, version = self.access(upload_id)
            deadline = time.monotonic() + 30
            def check():
                if time.monotonic() > deadline:
                    raise PlatformError("query_timeout", "素材查询超过本次时限，请稍后核验")
                self.access(upload_id, version)
            result = self.client.find_video(token, advertiser_id, signature, material_id, video_id, check)
            if result is None:
                raise PlatformError("not_visible", "暂未在目标账户查到素材，请稍后核验；不会自动重复上传")
            remote_material = identifier(result["material_id"])
            remote_video = result.get("id", "")
            if not isinstance(remote_video, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", remote_video):
                raise PlatformError("invalid_response", "平台素材标识格式异常")
            self.update(upload_id, status="succeeded", phase="", material_id=remote_material, video_id=remote_video,
                        next_attempt_at=None, error_code="", error_message="", provider_request_id="")
        except Exception as exc:
            error = exc if isinstance(exc, PlatformError) else PlatformError("verify_error", "素材核验失败，请检查授权后重新核验")
            self.update(upload_id, status=status, phase="verifying", error_code=error.code, error_message=error.message,
                provider_request_id=error.request_id,
                next_attempt_at=datetime.utcnow() + timedelta(seconds=5 * attempt) if attempt < 3 else None)

    def recover(self):
        with self.sessions() as db:
            db.query(Upload).filter_by(status="running", phase="preparing").update({"status": "queued", "phase": ""})
            db.query(Upload).filter_by(status="running", phase="uploading").update({"status": "unknown",
                "phase": "verifying", "next_attempt_at": datetime.utcnow()})
            # A crash during verification may have cleared its next execution time.
            db.query(Upload).filter(Upload.status.in_(["uploaded", "unknown"]), Upload.verify_attempts < 3).update(
                {"next_attempt_at": datetime.utcnow()})
            db.commit()
