"""Persistent object storage with an ephemeral local processing cache."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import tempfile
import uuid
from pathlib import Path

import aiofiles

from app.config import get_settings

OBJECT_ROUTE = "/api/storage/objects/"


def _token(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def object_key(url: str) -> str | None:
    if not url.startswith(OBJECT_ROUTE):
        return None
    value = url.removeprefix(OBJECT_ROUTE)
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


class AliyunOSSProvider:
    def __init__(self) -> None:
        import oss2

        settings = get_settings()
        required = {
            "ALIYUN_OSS_ENDPOINT": settings.aliyun_oss_endpoint,
            "ALIYUN_OSS_ACCESS_KEY_ID": settings.aliyun_oss_access_key_id,
            "ALIYUN_OSS_ACCESS_KEY_SECRET": settings.aliyun_oss_access_key_secret,
            "ALIYUN_OSS_BUCKET_NAME": settings.aliyun_oss_bucket_name,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError("OSS 配置缺失：" + ", ".join(missing))
        endpoint = settings.aliyun_oss_endpoint.removeprefix("https://").removeprefix("http://")
        self.bucket = oss2.Bucket(
            oss2.Auth(settings.aliyun_oss_access_key_id, settings.aliyun_oss_access_key_secret),
            f"https://{endpoint}", settings.aliyun_oss_bucket_name,
        )
        self.prefix = settings.aliyun_oss_prefix.strip("/")
        self.cache = Path(tempfile.gettempdir()) / "enrui-ai-commerce-agent-cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    def _new_key(self, filename: str, subdir: str) -> str:
        suffix = Path(filename).suffix
        relative = f"{subdir.strip('/')}/{uuid.uuid4().hex}{suffix}"
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def save_bytes(self, data: bytes, filename: str, subdir: str) -> str:
        key = self._new_key(filename, subdir)
        headers = {}
        content_type = mimetypes.guess_type(filename)[0]
        if content_type:
            headers["Content-Type"] = content_type
        self.bucket.put_object(key, data, headers=headers)
        return OBJECT_ROUTE + _token(key)

    def save_path(self, path: Path, filename: str, subdir: str) -> str:
        key = self._new_key(filename, subdir)
        headers = {}
        content_type = mimetypes.guess_type(filename)[0]
        if content_type:
            headers["Content-Type"] = content_type
        self.bucket.put_object_from_file(key, str(path), headers=headers)
        return OBJECT_ROUTE + _token(key)

    def local_path(self, url: str) -> Path | None:
        key = object_key(url)
        if not key:
            return None
        suffix = Path(key).suffix
        path = self.cache / f"{hashlib.sha256(key.encode()).hexdigest()}{suffix}"
        if not path.exists():
            self.bucket.get_object_to_file(key, str(path))
        return path

    def delete_sync(self, url: str) -> None:
        key = object_key(url)
        if key:
            self.bucket.delete_object(key)
            suffix = Path(key).suffix
            path = self.cache / f"{hashlib.sha256(key.encode()).hexdigest()}{suffix}"
            path.unlink(missing_ok=True)

    def signed_url(self, url: str) -> str:
        key = object_key(url)
        if not key:
            raise ValueError("不是 OSS 对象地址")
        return self.bucket.sign_url("GET", key, 900, slash_safe=True)


class StorageService:
    def __init__(self) -> None:
        mode = get_settings().storage_provider
        if mode not in {"aliyun_oss", "aliyun_oss_mirror"}:
            raise RuntimeError("仅支持阿里云 OSS 存储，请配置 STORAGE_PROVIDER=aliyun_oss")
        self.provider = AliyunOSSProvider()

    async def upload(self, data: bytes, filename: str, subdir: str) -> str:
        return await asyncio.to_thread(self.provider.save_bytes, data, filename, subdir)

    async def upload_path(self, path: Path, filename: str, subdir: str) -> str:
        return await asyncio.to_thread(self.provider.save_path, path, filename, subdir)

    def save_bytes(self, data: bytes, filename: str, subdir: str) -> str:
        return self.provider.save_bytes(data, filename, subdir)

    def local_path(self, url: str) -> Path | None:
        return self.provider.local_path(url)

    async def delete(self, url: str) -> None:
        await asyncio.to_thread(self.provider.delete_sync, url)

    def signed_url(self, url: str) -> str:
        return self.provider.signed_url(url)


_storage: StorageService | None = None


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
