from __future__ import annotations

import tempfile
import time
import unittest
import os
from pathlib import Path

from unittest.mock import patch

from app.services.storage import (
    AliyunOSSProvider,
    _provider_object_url,
    object_ref,
    private_video_enabled,
)


class StorageCacheCleanupTests(unittest.TestCase):
    def _provider(self, root: Path, max_bytes: int, ttl_seconds: int) -> AliyunOSSProvider:
        provider = AliyunOSSProvider.__new__(AliyunOSSProvider)
        provider.cache = root
        provider.cache_max_bytes = max_bytes
        provider.cache_ttl_seconds = ttl_seconds
        return provider

    def test_removes_expired_files_but_keeps_parts_and_protected_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expired = root / "expired.mp4"
            protected = root / "protected.mp4"
            partial = root / "download.mp4.token.part"
            for path in (expired, protected, partial):
                path.write_bytes(b"1234")
            old = time.time() - 7200
            for path in (expired, protected, partial):
                path.touch()
                path.chmod(0o600)
                os.utime(path, (old, old))

            self._provider(root, max_bytes=100, ttl_seconds=3600)._cleanup_cache(protected)

            self.assertFalse(expired.exists())
            self.assertTrue(protected.exists())
            self.assertTrue(partial.exists())

    def test_evicts_oldest_files_until_under_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oldest = root / "oldest.mp4"
            newest = root / "newest.mp4"
            oldest.write_bytes(b"1" * 8)
            newest.write_bytes(b"2" * 8)
            now = time.time()
            os.utime(oldest, (now - 20, now - 20))
            os.utime(newest, (now - 10, now - 10))

            self._provider(root, max_bytes=8, ttl_seconds=0)._cleanup_cache()

            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())


class StorageRoutingTests(unittest.TestCase):
    def test_legacy_object_url_defaults_to_aliyun(self) -> None:
        url = _provider_object_url("aliyun_oss", "video/a.mp4")
        self.assertEqual(object_ref(url), ("aliyun_oss", "video/a.mp4"))

    def test_private_object_url_carries_provider(self) -> None:
        url = _provider_object_url("private_s3", "video/a.mp4")
        self.assertEqual(object_ref(url), ("private_s3", "video/a.mp4"))

    def test_private_video_tenant_matches_id_or_code_only(self) -> None:
        settings = type("Settings", (), {"private_video_tenants": "dlr, tenant-2"})()
        with patch("app.services.storage.get_settings", return_value=settings):
            self.assertTrue(private_video_enabled("tenant-1", "DLR"))
            self.assertTrue(private_video_enabled("tenant-2", "other"))
            self.assertFalse(private_video_enabled("tenant-3", "other"))


if __name__ == "__main__":
    unittest.main()
