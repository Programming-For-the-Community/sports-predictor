"""
Enforces a minimum gap between ANY two calls, across all threads sharing
one instance. Used to keep a sport adapter polite to a free/unofficial
API when a backfill job runs several concurrent workers -- without a
shared limiter, N concurrent workers multiply the request rate by N
against a host the project doesn't control.
"""
import threading
import time


class RateLimiter:
    def __init__(self, min_interval_seconds: float):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.monotonic()
