"""
Client for CollegeFootballData.com's REST API v2 (api.collegefootballdata.com).
Unlike ESPN's keyless site API, CFBD requires an API key, resolved once
from AWS Secrets Manager at cold start (see _resolve_api_key) and cached
in memory for the container's lifetime -- the key itself never appears in
Terraform state or CI logs, only its Secrets Manager ARN does.

Not sport-path-parameterized like EspnBaseClient -- CFBD is a football-
only API with one flat set of endpoints, so this extends HttpClient
directly, the same way EspnCoreApiClient does for its own single-host,
no-sport-path shape.

Endpoint shapes documented below are verified against real CFBD
responses.
"""
import json
import os

import boto3

from library.http.client import HttpClient

DEFAULT_CFBD_API_ROOT_URL = "https://api.collegefootballdata.com"

_secrets_client = None
_cached_api_key: str | None = None


def _cfbd_root_url() -> str:
    return os.environ.get("CFBD_API_ROOT_URL", DEFAULT_CFBD_API_ROOT_URL).rstrip("/")


def _resolve_api_key() -> str:
    """Resolves this task's CFBD API key from the shared third-party-key
    secret (CFBD_API_KEY_SECRET_ARN), selecting CFBD_API_KEY_SECRET_FIELD's
    field -- e.g. "ncaa_fb_ingest_key" vs "ncaa_fb_backfill_key", so ingest
    and backfill never compete for the same monthly call budget even
    though both read the same secret. Cached at module level so a warm
    container only ever calls Secrets Manager once."""
    global _secrets_client, _cached_api_key
    if _cached_api_key is not None:
        return _cached_api_key

    secret_arn = os.environ["CFBD_API_KEY_SECRET_ARN"]
    field = os.environ["CFBD_API_KEY_SECRET_FIELD"]

    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    secret = json.loads(_secrets_client.get_secret_value(SecretId=secret_arn)["SecretString"])
    _cached_api_key = secret[field]
    return _cached_api_key


class CFBDClient(HttpClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(base_url=_cfbd_root_url(), min_interval_seconds=min_interval_seconds)
        self._session.headers["Authorization"] = f"Bearer {_resolve_api_key()}"

    def get_games(self, year: int, week: int | None = None, season_type: str = "regular") -> list[dict]:
        """One week's games (the whole season if week is omitted) -- flat
        per-game dicts with id/homeId/awayId/homePoints/awayPoints/
        homeConference/awayConference/conferenceGame/startDate/completed/
        homeClassification/awayClassification. classification=fbs scopes
        this project's FBS-only coverage, meaning "at least one side is
        FBS" (an FBS team's scheduled game against an FCS opponent still
        comes back), not "both sides FBS"."""
        params = {"year": year, "seasonType": season_type, "classification": "fbs"}
        if week is not None:
            params["week"] = week
        return self._get("games", params=params)

    def get_game_player_stats(self, year: int, week: int, season_type: str = "regular") -> list[dict]:
        """One week's player box scores in one call: [{"id": game_id,
        "teams": [{"team": school_name,
        "homeAway": ..., "categories": [{"name": ..., "types": [{"name":
        ..., "athletes": [{"id", "name", "stat"}]}]}]}]}]. No numeric team
        id or game date at this level -- callers must cross-reference "id"
        against get_games()'s own "id" to resolve home_id/away_id/
        event_date (see aws-lambdas/ncaafb/ingest/handler.py's
        _annotate_box_scores)."""
        return self._get("games/players", params={
            "year": year, "week": week, "seasonType": season_type, "classification": "fbs",
        })

    def get_game_team_stats(self, year: int, week: int, season_type: str = "regular") -> list[dict]:
        """One week's team box scores (turnovers, possession time, etc.)
        in one call: [{"id": game_id,
        "teams": [{"teamId", "team": school_name, "conference", "homeAway",
        "points", "stats": [{"category", "stat"}]}]}]. Unlike
        get_game_player_stats, each team block DOES carry its own numeric
        teamId. Some stats pack two numbers into one dash-separated string
        (e.g. "thirdDownEff": "7-10"), same convention ESPN's own team box
        scores use -- see library/normalize/ncaafb.py's
        _TEAM_STAT_COMPOUND_SPLITS. No event date on this endpoint's own
        entries -- only "id" and "teams"."""
        return self._get("games/teams", params={
            "year": year, "week": week, "seasonType": season_type, "classification": "fbs",
        })

    def get_coaches(self, year: int) -> list[dict]:
        """Every FBS coach for `year` in one call: [{"firstName", "lastName", "hireDate", "seasons":
        [{"school", "year", "games", "wins", "losses", "ties", ...}]}]. No
        numeric team or coach id -- team association is by school name
        only, via each season entry's own "school" field."""
        return self._get("coaches", params={"year": year})

    def get_rankings(self, year: int, week: int | None = None, season_type: str = "regular") -> list[dict]:
        """[{"season", "seasonType", "week",
        "polls": [{"poll": "AP Top 25", "ranks": [{"rank", "school",
        "conference", ...}]}, ...]}] -- includes non-FBS polls
        ("FCS Coaches Poll" etc.) in the same payload; callers must filter
        polls to "AP Top 25" specifically for FBS scope. Absent/empty for
        a week whose polls haven't been released yet (preseason, or a
        future week)."""
        params = {"year": year, "seasonType": season_type}
        if week is not None:
            params["week"] = week
        return self._get("rankings", params=params)

    def get_teams(self, year: int) -> list[dict]:
        """Every FBS team for `year` in one call, no classification filter
        needed. [{"id", "school",
        "conference", "location": {"dome": bool, "grass": bool, ...}}]."""
        return self._get("teams", params={"year": year})

    def get_calendar(self, year: int) -> list[dict]:
        """Week date-boundaries for `year`:
        [{"season", "week", "seasonType", "startDate", "endDate",
        "firstGameStart", "lastGameStart"}, ...]. Used by ingest/
        handler.py to resolve "today" into a season/week, the same role
        NFL's _most_recent_sunday plays via ESPN's per-date scoreboard
        lookup -- CFBD has no equivalent single-call date lookup to
        delegate this to."""
        return self._get("calendar", params={"year": year})

    def get_roster(self, year: int) -> list[dict]:
        """Every FBS (and other-division) team's full roster in one bulk
        call (~9MB). Fetched by ingest/handler.py on a monthly cadence,
        refreshed early if the season has already started."""
        return self._get("roster", params={"year": year})
