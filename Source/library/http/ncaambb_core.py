"""
Client for ESPN's "core" API (sports.core.api.espn.com), scoped to men's
college basketball -- AP Top 25 poll history, used to label the national-
ranking model's training rows (see library/features/ncaambb.py's
build_team_week_features and model-training/ncaambb/train_ranking_model.py).

Not built as an addition to library/http/espn_core.py: that module's
DEFAULT_ESPN_CORE_API_ROOT_URL is hardcoded to NFL's league path and its
methods (coaches, injuries) are NFL-specific -- extending it into a
generic multi-sport class is a bigger refactor of working code than this
one new endpoint justifies. Extends HttpClient directly, same reasoning
espn_core.py's own docstring gives for doing the same.

Confirmed live, 2026-08-20 (see project-ncaambb-onboarding memory):
- seasons/{season}/types/{season_type}/weeks/{week}/rankings/1 is
  reliably the AP Top 25 poll (id "1"), confirmed stable across seasons
  2018/2020/2025 -- no need to list-then-resolve every poll id per week
  the way espn_core.py's get_season_coaches has to.
- A week with no released poll (most out-of-range weeks probed while
  walking a season) returns a clean 404, not an error shape -- get_ap_poll
  makes exactly ONE request and treats any non-200 as "no poll," rather
  than going through this project's usual retry-with-backoff (which
  exists for a transient failure on data known to exist, not for probing
  a range where most high week numbers legitimately have nothing). This
  is also why get_ap_poll is NOT built on top of HttpClient._get -- that
  method's shared _request() retries every non-2xx, which would burn 5
  attempts' worth of backoff (~46s) on every single legitimately-absent
  week.
- The site API's own /rankings response (see NCAAMBBClient in
  library/http/ncaambb.py, a different host/client) points at this host
  via $ref, but on an internal-only sports.core.api.espn.pvt hostname
  that doesn't resolve publicly -- season/type/week are parsed out of
  that ref's path instead of following it, and re-requested against this
  client's own public host.
"""
import os
import re

from library.http.client import HttpClient, REQUEST_TIMEOUT_SECONDS

DEFAULT_ESPN_CORE_API_ROOT_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"

_TRAILING_ID_RE = re.compile(r"/(\d+)(?:\?|$)")
_SEASON_TYPE_WEEK_RE = re.compile(r"/seasons/(\d+)/types/(\d+)/weeks/(\d+)/")


def _espn_core_root_url() -> str:
    return os.environ.get("NCAAMBB_ESPN_CORE_API_ROOT_URL", DEFAULT_ESPN_CORE_API_ROOT_URL).rstrip("/")


def _id_from_ref(ref_url: str | None) -> str | None:
    """Every ESPN core-API $ref ends in .../<numeric id>?lang=en&region=us
    -- same trailing-id convention library/http/espn_core.py's own
    _id_from_ref relies on. Duplicated rather than imported: espn_core.py
    is NFL-scoped and importing a private helper across an otherwise
    unrelated sport module would be a stranger coupling than one small
    regex living in both."""
    if not ref_url:
        return None
    match = _TRAILING_ID_RE.search(ref_url)
    return match.group(1) if match else None


def season_type_week_from_ref(ref_url: str | None) -> tuple[int, int, int] | None:
    """Parses (season, season_type, week) out of a core-API ranking $ref's
    path -- see this module's own docstring for why the ref itself is
    never followed directly (internal-only .pvt hostname)."""
    if not ref_url:
        return None
    match = _SEASON_TYPE_WEEK_RE.search(ref_url)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def ap_poll_to_rank_by_team(poll: dict) -> dict[str, int]:
    """{team_id: rank} from a raw AP poll response's own `ranks` list --
    team_id is parsed out of each entry's team.$ref (same trailing-id
    convention as _id_from_ref), not a second dereferencing call."""
    result = {}
    for entry in poll.get("ranks", []):
        team_id = _id_from_ref(entry.get("team", {}).get("$ref"))
        rank = entry.get("current")
        if team_id is not None and rank is not None:
            result[team_id] = rank
    return result


class NCAAMBBCoreClient(HttpClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(base_url=_espn_core_root_url(), min_interval_seconds=min_interval_seconds)

    def get_ap_poll(self, season: int, season_type: int, week: int) -> dict | None:
        """The raw AP Top 25 poll for one (season, season_type, week), or
        None if no poll was released that week -- see this module's own
        docstring for why this deliberately makes one request rather than
        going through HttpClient._get's retry-with-backoff."""
        self._rate_limiter.wait()
        url = f"{self.base_url}/seasons/{season}/types/{season_type}/weeks/{week}/rankings/1"
        response = self._session.get(url, params={"lang": "en", "region": "us"}, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
