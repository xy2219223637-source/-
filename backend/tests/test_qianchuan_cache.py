import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from app.services.qianchuan.cache import ReportCache


class ReportCacheTests(unittest.TestCase):
    def test_expiration_is_one_hour_and_hits_do_not_extend_it(self):
        clock = [0]
        cache = ReportCache(clock=lambda: clock[0])
        self.assertEqual(cache.get("key", lambda: {"value": 1})[1], "miss")
        clock[0] = 3599
        result, status = cache.get("key", lambda: self.fail("unexpected load"))
        self.assertEqual(status, "hit")
        result["value"] = 99
        self.assertEqual(cache.get("key", lambda: None)[0]["value"], 1)
        clock[0] = 3600
        self.assertEqual(cache.get("key", lambda: {"value": 2}), ({"value": 2}, "miss"))

    def test_same_key_requests_join_but_other_keys_are_independent(self):
        self.check_concurrency(False)

    def test_failed_loader_releases_waiters_and_is_not_cached(self):
        self.check_concurrency(True)

    def check_concurrency(self, fail):
        cache = ReportCache()
        started, joined, release = Event(), Event(), Event()
        class ObservedFuture(Future):
            def result(self, *args, **kwargs):
                joined.set()
                return super().result(*args, **kwargs)
        def load():
            started.set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            if fail:
                raise ValueError("test failure")
            return {"value": 1}
        with patch("app.services.qianchuan.cache.Future", ObservedFuture), ThreadPoolExecutor(2) as pool:
            first = pool.submit(cache.get, "same", load)
            try:
                self.assertTrue(started.wait(5))
                second = pool.submit(cache.get, "same", lambda: self.fail("duplicate load"))
                self.assertTrue(joined.wait(5))
                self.assertEqual(cache.get("other", lambda: 2), (2, "miss"))
            finally:
                release.set()
            if fail:
                for future in (first, second):
                    with self.assertRaises(ValueError):
                        future.result(timeout=5)
            else:
                self.assertEqual(first.result(timeout=5), ({"value": 1}, "miss"))
                self.assertEqual(second.result(timeout=5), ({"value": 1}, "joined"))
        if fail:
            self.assertEqual(cache.get("same", lambda: 3), (3, "miss"))

    def test_capacity_evicts_least_recently_used(self):
        cache = ReportCache(capacity=2)
        cache.get("a", lambda: 1)
        cache.get("b", lambda: 2)
        cache.get("a", lambda: None)
        cache.get("c", lambda: 3)
        self.assertEqual(cache.get("b", lambda: 4), (4, "miss"))
