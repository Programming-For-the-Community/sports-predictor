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
    def __init__(self, base_url: str, min_interval_seconds: float = 0.3, user_agent: str | None = None):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        # A bare, self-identifying User-Agent like "sports-predictor/1.0" is
        # exactly the kind of signature a WAF/CDN in front of an
        # "unofficial" API blocks outright, and a 403 isn't retried away by
        # backoff since it's the same block on every attempt. Defaulting to
        # a real browser's UA + standard Accept headers is standard
        # practice for a public API with no official client library, not
        # an attempt to evade anything -- this project only ever reads
        # public scoreboard/box-score data the same page a real visitor's
        # browser would load.
        self._session.headers.update({
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._rate_limiter = RateLimiter(min_interval_seconds)

    def _get(self, path: str, params: dict) -> dict:
        return self._request(f"{self.base_url}/{path.lstrip('/')}", params)

    def get_absolute(self, url: str, params: dict | None = None) -> dict:
        """Same retry/rate-limit behavior as _get, but against an arbitrary
        absolute URL rather than one built from self.base_url -- for
        dereferencing a `$ref` a list-style response returned (ESPN's
        sports.core.api.espn.com host returns links, not embedded data;
        see library/http/espn_core.py), which by definition isn't under
        this client's own base_url."""
        return self._request(url, params or {})

    def _request(self, url: str, params: dict) -> dict:
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
                    attempt, MAX_RETRIES, url, params, exc, sleep_for,
                )
                time.sleep(sleep_for)
        raise RuntimeError(f"Request to {url} {params} failed after {MAX_RETRIES} attempts") from last_exc
