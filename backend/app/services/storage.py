"""Persistent object storage with an ephemeral local processing cache."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import aiofiles

from app.config import get_settings

OBJECT_ROUTE = "/api/storage/objects/"
logger = logging.getLogger(__name__)


def _token(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def object_key(url: str) -> Optional[str]:
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
        self.cache = (
            Path(settings.storage_cache_dir).expanduser()
            if settings.storage_cache_dir.strip()
            else Path(tempfile.gettempdir()) / "enrui-ai-commerce-agent-cache"
        )
        self.cache_max_bytes = max(0, int(settings.storage_cache_max_gb * 1024 ** 3))
        self.cache_ttl_seconds = max(0, int(settings.storage_cache_ttl_hours * 3600))
        self.cache.mkdir(parents=True, exist_ok=True)

    def _cleanup_cache(self, protected: Optional[Path] = None) -> None:
        """Best-effort TTL/LRU cleanup; cached objects are always recoverable from OSS."""
        try:
            now = time.time()
            entries = []
            total = 0
            for path in self.cache.iterdir():
                if not path.is_file() or ".part" in path.name or path == protected:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if self.cache_ttl_seconds and now - stat.st_mtime > self.cache_ttl_seconds:
                    try:
                        path.unlink()
                    except OSError:
                        logger.warning("Failed to remove expired storage cache file: %s", path, exc_info=True)
                    continue
                total += stat.st_size
                entries.append((stat.st_mtime, stat.st_size, path))

            if self.cache_max_bytes and total > self.cache_max_bytes:
                for _, size, path in sorted(entries):
                    try:
                        path.unlink()
                        total -= size
                    except OSError:
                        logger.warning("Failed to remove storage cache file: %s", path, exc_info=True)
                    if total <= self.cache_max_bytes:
                        break
        except OSError:
            logger.warning("Storage cache cleanup failed", exc_info=True)

    def _cache_path(self, key: str, expected_size: int, etag: str) -> Path:
        suffix = Path(key).suffix
        version = f"{key}:{etag}:{expected_size}"
        return self.cache / f"{hashlib.sha256(version.encode()).hexdigest()}{suffix}"

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

    def local_path(self, url: str) -> Optional[Path]:
        key = object_key(url)
        if not key:
            return None
        head = self.bucket.head_object(key)
        expected_size = int(head.content_length)
        path = self._cache_path(key, expected_size, str(getattr(head, "etag", "")))
        self._cleanup_cache(protected=path)
        if path.exists() and path.stat().st_size != expected_size:
            path.unlink(missing_ok=True)
        if not path.exists():
            free_bytes = shutil.disk_usage(self.cache).free
            if expected_size > free_bytes:
                raise IOError(f"本地缓存空间不足：需要 {expected_size} 字节，可用 {free_bytes} 字节")
            partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
            try:
                self.bucket.get_object_to_file(key, str(partial))
                actual_size = partial.stat().st_size
                if actual_size != expected_size:
                    raise IOError(f"OSS 下载不完整：期望 {expected_size} 字节，实际 {actual_size} 字节")
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        try:
            os.utime(path, None)
        except OSError:
            logger.warning("Failed to update storage cache access time: %s", path, exc_info=True)
        self._cleanup_cache(protected=path)
        return path

    def delete_sync(self, url: str) -> None:
        key = object_key(url)
        if key:
            path = None
            try:
                head = self.bucket.head_object(key)
                path = self._cache_path(key, int(head.content_length), str(getattr(head, "etag", "")))
            except Exception:
                pass
            self.bucket.delete_object(key)
            if path:
                path.unlink(missing_ok=True)
            # Remove cache entries created before version-aware naming was introduced.
            legacy = self.cache / f"{hashlib.sha256(key.encode()).hexdigest()}{Path(key).suffix}"
            legacy.unlink(missing_ok=True)

    def signed_url(self, url: str) -> str:
        key = object_key(url)
        if not key:
            raise ValueError("不是 OSS 对象地址")
        return self.bucket.sign_url("GET", key, 900, slash_safe=True)

    def begin_multipart(self, filename: str, subdir: str, content_type: str) -> tuple[str, str]:
        key = self._new_key(filename, subdir)
        headers = {"Content-Type": content_type} if content_type else {}
        result = self.bucket.init_multipart_upload(key, headers=headers)
        return key, result.upload_id

    def signed_part_url(self, key: str, upload_id: str, part_number: int) -> str:
        return self.bucket.sign_url(
            "PUT",
            key,
            3600,
            headers={"Content-Type": "application/octet-stream"},
            params={"uploadId": upload_id, "partNumber": str(part_number)},
            slash_safe=True,
        )

    def complete_multipart(self, key: str, upload_id: str) -> tuple[str, int, str]:
        import oss2
        listed = self.bucket.list_parts(key, upload_id)
        parts = [oss2.models.PartInfo(item.part_number, item.etag) for item in listed.parts]
        result = self.bucket.complete_multipart_upload(key, upload_id, parts)
        head = self.bucket.head_object(key)
        return OBJECT_ROUTE + _token(key), int(head.content_length), str(result.etag or head.etag)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.bucket.abort_multipart_upload(key, upload_id)


class StorageService:
    def __init__(self) -> None:
        mode = get_settings().storage_provider
        if mode not in {"aliyun_oss", "aliyun_oss_mirror"}:
            raise RuntimeError("仅支持阿里云 OSS 存储，请配置 STORAGE_PROVIDER=aliyun_oss")
        self.provider = AliyunOSSProvider()

    async def upload(self, data: bytes, filename: str, subdir: str) -> str:
        return await asyncio.to_thread(self.provider.save_bytes, data, filename, subdir)

    def save_bytes(self, data: bytes, filename: str, subdir: str) -> str:
        return self.provider.save_bytes(data, filename, subdir)

    def local_path(self, url: str) -> Optional[Path]:
        return self.provider.local_path(url)

    async def delete(self, url: str) -> None:
        await asyncio.to_thread(self.provider.delete_sync, url)

    def signed_url(self, url: str) -> str:
        return self.provider.signed_url(url)

    async def begin_multipart(self, filename: str, subdir: str, content_type: str) -> tuple[str, str]:
        return await asyncio.to_thread(self.provider.begin_multipart, filename, subdir, content_type)

    def signed_part_url(self, key: str, upload_id: str, part_number: int) -> str:
        return self.provider.signed_part_url(key, upload_id, part_number)

    async def complete_multipart(self, key: str, upload_id: str) -> tuple[str, int, str]:
        return await asyncio.to_thread(self.provider.complete_multipart, key, upload_id)

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        await asyncio.to_thread(self.provider.abort_multipart, key, upload_id)


_storage: Optional[StorageService] = None


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
