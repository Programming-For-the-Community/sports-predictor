"""
Shared test fixtures for test_ingest_*.py's own split -- see
test_ingest_lambda_handler.py's own module docstring for why this lives
in its own importable (not test_-prefixed, so pytest never collects it as
a test module itself) file instead of being duplicated across every
split file: _make_s3 in particular is a substantial in-memory S3 mock,
not a one-line builder, so copy-pasting it five times would work against
"more maintainable" rather than for it.
"""
import json
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

SEASON_YEAR = 2025
SEASON_TYPE = 2


def completed_event(event_id: str) -> dict:
    return {"id": event_id, "status": {"type": {"completed": True}}}


def incomplete_event(event_id: str) -> dict:
    return {"id": event_id, "status": {"type": {"completed": False}}}


def scoreboard(events: list, week: int = 5, season_year: int = SEASON_YEAR, season_type: int = SEASON_TYPE) -> dict:
    """Matches site.web.api.espn.com's real shape (confirmed via curl) --
    no top-level "season" key; year/type live under leagues[0].season, and
    type is itself a dict, not a bare int. week stays top-level."""
    return {
        "week": {"number": week},
        "leagues": [{"season": {"year": season_year, "type": {"id": str(season_type), "type": season_type}}}],
        "events": events,
    }


def make_s3(existing_keys: set | None = None):
    """Return a mock S3 client backed by an in-memory dict -- head_object/
    get_object see whatever's actually been put_object'd (including by
    enrichment's own cache writes), and 404 for anything else.
    `existing_keys` seeds head_object-only existence (no real body) for
    tests that only ever check existence."""
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _head(**kwargs):
        key = kwargs.get("Key")
        if key in store or key in (existing_keys or set()):
            return {}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body.encode("utf-8") if isinstance(body, str) else body

    mock_s3.head_object.side_effect = _head
    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    return mock_s3


def teams_response(*team_ids: str) -> dict:
    """Matches NFLClient.get_teams' real shape (confirmed via curl):
    sports[0].leagues[0].teams, each a {"team": {"id": ...}} wrapper."""
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": tid}} for tid in team_ids]}]}]}


def make_client(board: dict, summary: dict | None = None):
    mock = MagicMock()
    mock.get_scoreboard_for_date.return_value = board
    mock.get_scoreboard.return_value = board
    mock.get_summary.return_value = summary or {"header": {}, "boxscore": {}}
    mock.get_depth_chart.return_value = {"depthchart": []}
    mock.get_roster.return_value = {"team": {"id": "0"}, "timestamp": "2026-08-08T00:00:00Z", "athletes": []}
    # Empty by default -- most tests here aren't exercising roster fetching
    # (unconditional in lambda_handler, see _fetch_rosters), they just
    # need it to run without error. test_ingest_rosters.py sets this
    # explicitly.
    mock.get_teams.return_value = teams_response()
    return mock


def make_core_client():
    """Sensible empty-but-well-formed defaults -- most tests here aren't
    exercising enrichment at all (their fixture events have no
    "competitions" key, so enrichment.home_away_team_ids returns None and
    the per-team fetch loop never runs), they just need lambda_handler's
    unconditional EspnCoreApiClient() construction to not make a real
    network call."""
    mock = MagicMock()
    mock.get_season_coaches.return_value = {}
    mock.get_team_injuries.return_value = []
    return mock
