"""
Base HTTP client for free/unofficial sport data APIs: retry-with-backoff
on failure, and a shared RateLimiter so concurrent callers don't multiply
the request rate against a host the project doesn't control. Subclass
this per data source (see Source/data-backfills/nfl/espn_client.py) and
add source-specific endpoint methods -- the retry/pacing behavior below
is the same regardless of which API is being called.
"""
import logging
import time

import requests

from library.http.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 15


class HttpClient:
    def __init__(self, base_url: str, min_interval_seconds: float = 0.3, user_agent: str = "sports-predictor/1.0"):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._rate_limiter = RateLimiter(min_interval_seconds)

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limiter.wait()
            try:
                response = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_exc = exc
                sleep_for = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Request failed (attempt %d/%d) for %s %s: %s -- retrying in %.1fs",
                    attempt, MAX_RETRIES, path, params, exc, sleep_for,
                )
                time.sleep(sleep_for)
        raise RuntimeError(f"Request to {url} {params} failed after {MAX_RETRIES} attempts") from last_exc
