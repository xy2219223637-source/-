"""Bounded process-local TTL cache with one loader per key."""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
from time import monotonic


class ReportCache:
    def __init__(self, ttl=3600, capacity=512, clock=monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._values = OrderedDict()
        self._pending = {}
        self._lock = Lock()

    def get(self, key, load):
        with self._lock:
            now = self.clock()
            for expired in [k for k, (deadline, _) in self._values.items() if deadline <= now]:
                del self._values[expired]
            if key in self._values:
                self._values.move_to_end(key)
                return deepcopy(self._values[key][1]), "hit"
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._pending[key] = future
        if not owner:
            return deepcopy(future.result()), "joined"
        try:
            value = load()
            with self._lock:
                self._values[key] = (self.clock() + self.ttl, deepcopy(value))
                while len(self._values) > self.capacity:
                    self._values.popitem(last=False)
            future.set_result(value)
            return deepcopy(value), "miss"
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)
