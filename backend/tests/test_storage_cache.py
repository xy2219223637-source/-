from __future__ import annotations

import tempfile
import time
import unittest
import os
from pathlib import Path

from app.services.storage import AliyunOSSProvider


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


if __name__ == "__main__":
    unittest.main()
