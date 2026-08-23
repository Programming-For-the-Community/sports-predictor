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
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from library.http.client import HttpClient, REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# One call per conference (31 as of the 2026 season, see
# get_conference_group_refs) to fetch its own teams ref -- small enough
# that ingest's own _INGEST_MAX_WORKERS=8 convention is plenty here too.
_CONFERENCE_MEMBERSHIP_MAX_WORKERS = 8

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


def current_ap_poll_pointer(rankings_response: dict) -> tuple[int, int, int] | None:
    """Picks the AP entry out of NCAAMBBClient.get_current_rankings_pointer's
    response (matched by its own `type` field, not position -- confirmed
    live the AP entry is always first but that's not a contract worth
    depending on) and resolves it to (season, season_type, week) via
    season_type_week_from_ref. None if no AP entry is present at all."""
    for entry in rankings_response.get("rankings", []):
        if entry.get("type") == "ap":
            return season_type_week_from_ref(entry.get("$ref"))
    return None


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


# NCAA Division I's own group id, one level below the (unused here)
# all-divisions root -- confirmed live, 2026-08-22, the same id
# NCAAMBBClient.get_scoreboard_for_date's own groups=50 param already
# relies on. Stable across seasons (it's ESPN's id for the division
# itself, not for any one year's membership of it).
DIVISION_I_GROUP_ID = "50"


def _refs(listing: dict) -> list[str]:
    return [item["$ref"] for item in listing.get("items", [])]


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

    def get_conference_group_refs(self, season: int, season_type: int = 2) -> list[str]:
        """$ref URLs for every group directly under Division I this season
        -- confirmed live, 2026-08-22: 31 items for season 2026/type 2,
        each one a real conference (isConference=true; see
        get_group_detail). season_type=2 (regular season) is the default
        since conference membership doesn't change mid-season the way a
        team's own postseason `groups` pointer sometimes reads (a Duke
        team lookup returned a types/3 ref despite this being fetched
        pre-tournament) -- type doesn't affect which conferences exist,
        only which snapshot of the hierarchy answers the query, and 2 is
        always in season.
        No caller should hardcode DIVISION_I_GROUP_ID's children count or
        identities -- real D1 realignment changes both across seasons,
        which is the whole reason this is resolved live instead of via a
        static table (see project-ncaambb-onboarding memory)."""
        url = f"{self.base_url}/seasons/{season}/types/{season_type}/groups/{DIVISION_I_GROUP_ID}/children"
        response = self._session.get(url, params={"lang": "en", "region": "us", "limit": 200}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return _refs(response.json())

    def get_group_detail(self, group_ref: str) -> dict:
        """Resolves one conference group ref (from get_conference_group_refs)
        to its name/shortName/isConference and its own `teams` ref -- the
        raw dict is returned as-is rather than picking fields, callers
        (resolve_conference_membership) know what they need."""
        self._rate_limiter.wait()
        response = self._session.get(group_ref, params={"lang": "en", "region": "us"}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    def get_group_team_refs(self, teams_ref: str) -> list[str]:
        """$ref URLs for every team in one conference this season, from a
        group detail's own `teams.$ref` (get_group_detail). Confirmed
        live, 2026-08-22: the ACC's own ref returns exactly its 18 current
        members (post-realignment), team ids parsed by the same trailing-
        id convention as _id_from_ref."""
        self._rate_limiter.wait()
        response = self._session.get(teams_ref, params={"lang": "en", "region": "us", "limit": 50}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return _refs(response.json())


def resolve_conference_membership(client: NCAAMBBCoreClient, season: int) -> dict[str, str]:
    """{team_id: conference_shortName} for every current D1 conference
    member this season -- derived live from ESPN's own group hierarchy
    (get_conference_group_refs -> get_group_detail -> get_group_team_refs),
    NOT a static table. Real D1 realignment (conferences gaining/losing
    members, or folding entirely) is picked up automatically the next time
    this runs, same spirit as NCAAFB's own home_conference/away_conference
    fields -- see project-ncaambb-onboarding memory for why a static table
    (NBA's own pattern) was rejected here.

    A team belonging to no conference this season (shouldn't happen for a
    real D1 member, but ESPN data has surprised this project before) is
    simply absent from the result rather than raising -- callers already
    treat "no known conference" as exclusion (see NCAAFB's own
    remaining_games filter in season_projection.py's
    _season_standings_inputs).

    One conference's fetch failing (a transient ESPN hiccup) logs and
    skips that conference rather than failing the whole resolution --
    partial membership data for one run is a better failure mode than
    losing every conference because of one bad response."""
    conference_refs = client.get_conference_group_refs(season)

    def _members(conference_ref: str) -> tuple[str, list[str]] | None:
        try:
            detail = client.get_group_detail(conference_ref)
            if not detail.get("isConference"):
                return None
            name = detail.get("shortName") or detail.get("name")
            teams_ref = detail.get("teams", {}).get("$ref")
            if name is None or teams_ref is None:
                return None
            team_refs = client.get_group_team_refs(teams_ref)
            team_ids = [team_id for team_id in (_id_from_ref(ref) for ref in team_refs) if team_id is not None]
            return name, team_ids
        except Exception:
            logger.exception("Failed resolving conference membership for group %s -- skipping", conference_ref)
            return None

    with ThreadPoolExecutor(max_workers=_CONFERENCE_MEMBERSHIP_MAX_WORKERS) as executor:
        results = list(executor.map(_members, conference_refs))

    team_conference: dict[str, str] = {}
    for result in results:
        if result is None:
            continue
        name, team_ids = result
        for team_id in team_ids:
            team_conference[team_id] = name
    return team_conference
