"""
Unit tests for the NFL ingest Lambda handler.

All AWS and ESPN calls are mocked -- these tests run without credentials
and verify handler logic: event routing, idempotency, error resilience,
and return-value counts. Coach/injury/depth-chart enrichment itself is
covered separately in test_enrichment.py.

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
import nfl_ingest
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completed_event(event_id: str) -> dict:
    return {"id": event_id, "status": {"type": {"completed": True}}}


def _incomplete_event(event_id: str) -> dict:
    return {"id": event_id, "status": {"type": {"completed": False}}}


SEASON_YEAR = 2025
SEASON_TYPE = 2


def _scoreboard(events: list, week: int = 5, season_year: int = SEASON_YEAR, season_type: int = SEASON_TYPE) -> dict:
    """Matches site.web.api.espn.com's real shape (confirmed via curl) --
    no top-level "season" key; year/type live under leagues[0].season, and
    type is itself a dict, not a bare int. week stays top-level."""
    return {
        "week": {"number": week},
        "leagues": [{"season": {"year": season_year, "type": {"id": str(season_type), "type": season_type}}}],
        "events": events,
    }


def _make_s3(existing_keys: set | None = None):
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


def _make_client(scoreboard: dict, summary: dict | None = None):
    mock = MagicMock()
    mock.get_scoreboard_for_date.return_value = scoreboard
    mock.get_scoreboard.return_value = scoreboard
    mock.get_summary.return_value = summary or {"header": {}, "boxscore": {}}
    mock.get_depth_chart.return_value = {"positions": {}}
    mock.get_roster.return_value = {"team": {"id": "0"}, "timestamp": "2026-08-08T00:00:00Z", "athletes": []}
    # Empty by default -- most tests here aren't exercising roster fetching
    # (now unconditional in lambda_handler, see _fetch_rosters), they just
    # need it to run without error. TestFetchRosters sets this explicitly.
    mock.get_teams.return_value = _teams_response()
    return mock


def _teams_response(*team_ids: str) -> dict:
    """Matches NFLClient.get_teams' real shape (confirmed via curl):
    sports[0].leagues[0].teams, each a {"team": {"id": ...}} wrapper."""
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": tid}} for tid in team_ids]}]}]}


def _make_core_client():
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIngestLambdaHandler:
    def test_processes_completed_events(self):
        board = _scoreboard([_completed_event("123")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["failed"] == 0
        mock_client.get_summary.assert_called_once_with("123")
        mock_s3.put_object.assert_called()

    def test_skips_incomplete_events(self):
        board = _scoreboard([_completed_event("1"), _incomplete_event("2")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["skipped"] == 1
        mock_client.get_summary.assert_called_once_with("1")

    def test_skips_events_already_in_s3(self):
        board = _scoreboard([_completed_event("123")])
        # Pre-mark the box score key as already present
        existing_key = f"nfl/boxscore/{SEASON_YEAR}/123.json"
        mock_s3 = _make_s3(existing_keys={existing_key})
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 0
        assert result["skipped"] == 1
        mock_client.get_summary.assert_not_called()

    def test_uses_explicit_week_from_event_payload(self):
        board = _scoreboard([])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            nfl_ingest.lambda_handler({"season": 2024, "season_type": 2, "week": 3}, None)

        mock_client.get_scoreboard.assert_called_once_with(2024, 2, 3)
        mock_client.get_scoreboard_for_date.assert_not_called()

    def test_auto_detects_week_when_not_in_payload(self):
        board = _scoreboard([], week=7)
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            nfl_ingest.lambda_handler({}, None)

        # Exact date value is covered by TestMostRecentSunday below --
        # here we only need to confirm the date-based lookup is used
        # instead of get_scoreboard's explicit-week path.
        mock_client.get_scoreboard_for_date.assert_called_once()
        mock_client.get_scoreboard.assert_not_called()

    def test_skips_preseason_given_explicitly(self):
        mock_s3 = _make_s3()
        mock_client = _make_client(_scoreboard([]))

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({"season": 2025, "season_type": 1, "week": 1}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0, "rosters_fetched": 0, "rosters_failed": 0}
        mock_client.get_scoreboard.assert_not_called()
        mock_client.get_scoreboard_for_date.assert_not_called()

    def test_skips_preseason_when_auto_detected(self):
        board = _scoreboard([_completed_event("1")], season_type=1)
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0, "rosters_fetched": 0, "rosters_failed": 0}
        mock_client.get_summary.assert_not_called()

    def test_fetches_rosters_even_during_preseason(self):
        # The one exception to "preseason isn't ingested" -- see
        # lambda_handler's own comment on why roster fetching runs before
        # this check rather than being skipped alongside everything else.
        mock_s3 = _make_s3()
        mock_client = _make_client(_scoreboard([]))
        mock_client.get_teams.return_value = _teams_response("12", "13")

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({"season": 2025, "season_type": 1, "week": 1}, None)

        assert result["rosters_fetched"] == 2
        mock_client.get_roster.assert_any_call("12")
        mock_client.get_roster.assert_any_call("13")

    def test_continues_after_individual_game_failure(self):
        board = _scoreboard([_completed_event("1"), _completed_event("2")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)
        mock_client.get_summary.side_effect = [Exception("ESPN timeout"), {"header": {}, "boxscore": {}}]

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1

    def test_returns_correct_aggregate_counts(self):
        board = _scoreboard([
            _completed_event("1"),  # processed
            _incomplete_event("2"),  # skipped (not done)
            _completed_event("3"),  # skipped (already in S3)
        ])
        existing_key = f"nfl/boxscore/{SEASON_YEAR}/3.json"
        mock_s3 = _make_s3(existing_keys={existing_key})
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 1, "skipped": 2, "failed": 0, "rosters_fetched": 0, "rosters_failed": 0}

    def test_returns_empty_counts_for_empty_scoreboard(self):
        board = _scoreboard([])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0, "rosters_fetched": 0, "rosters_failed": 0}
        mock_client.get_summary.assert_not_called()


class TestMostRecentSunday:
    # 2026-09-13 is a confirmed Sunday (live ESPN response for
    # dates=20260913 returned week 1, season.type 2, games at the classic
    # Sunday 1pm ET slot -- see the ingest handler's module docstring).
    SUNDAY = date(2026, 9, 13)

    def test_sunday_itself_returns_same_day(self):
        assert nfl_ingest._most_recent_sunday(self.SUNDAY) == "20260913"

    def test_monday_returns_previous_day(self):
        assert nfl_ingest._most_recent_sunday(date(2026, 9, 14)) == "20260913"

    def test_tuesday_returns_same_sunday_as_wednesday(self):
        tuesday = nfl_ingest._most_recent_sunday(date(2026, 9, 15))
        wednesday = nfl_ingest._most_recent_sunday(date(2026, 9, 16))

        assert tuesday == "20260913"
        assert wednesday == "20260913"
        assert tuesday == wednesday  # the whole point of this function

    def test_saturday_does_not_roll_forward_to_next_sunday(self):
        assert nfl_ingest._most_recent_sunday(date(2026, 9, 19)) == "20260913"

    def test_defaults_to_actual_today_when_not_given(self):
        # Just confirms the default path runs and returns the expected
        # format -- the date-math itself is covered by the fixed-date
        # cases above.
        result = nfl_ingest._most_recent_sunday()
        assert len(result) == 8
        assert result.isdigit()


class TestIngestHelpers:
    def test_object_exists_returns_true_when_key_found(self):
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ContentLength": 100}

        with patch.object(nfl_ingest, "_s3", mock_s3):
            assert nfl_ingest._object_exists("some/key.json") is True

    def test_object_exists_returns_false_on_client_error(self):
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        with patch.object(nfl_ingest, "_s3", mock_s3):
            assert nfl_ingest._object_exists("missing/key.json") is False

    def test_put_json_serialises_payload_correctly(self):
        mock_s3 = MagicMock()
        payload = {"events": [{"id": "123"}]}

        with patch.object(nfl_ingest, "_s3", mock_s3):
            nfl_ingest._put_json("nfl/test.json", payload)

        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Key"] == "nfl/test.json"
        assert call_kwargs["ContentType"] == "application/json"
        assert '"events"' in call_kwargs["Body"]


class TestAllTeamIds:
    def test_extracts_every_team_id_from_the_real_response_shape(self):
        client = MagicMock()
        client.get_teams.return_value = _teams_response("12", "13", "14")

        assert nfl_ingest._all_team_ids(client) == ["12", "13", "14"]

    def test_empty_leagues_returns_no_ids(self):
        client = MagicMock()
        client.get_teams.return_value = {"sports": [{"leagues": []}]}

        assert nfl_ingest._all_team_ids(client) == []


class TestFetchRosters:
    def test_fetches_and_writes_one_roster_per_team(self):
        client = _make_client(_scoreboard([]))
        client.get_teams.return_value = _teams_response("12", "13")
        mock_s3 = _make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert fetched == 2
        assert failed == 0
        client.get_roster.assert_any_call("12")
        client.get_roster.assert_any_call("13")
        assert client.get_roster.call_count == 2

    def test_fetches_every_team_regardless_of_any_weeks_schedule(self):
        # The whole point of sourcing team ids from get_teams instead of a
        # week's scoreboard -- this covers all 32, not just whichever
        # teams happen to have a game that week (or any games at all).
        client = _make_client(_scoreboard([]))
        client.get_teams.return_value = _teams_response(*[str(i) for i in range(1, 33)])
        mock_s3 = _make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert fetched == 32

    def test_writes_to_the_expected_s3_key(self):
        client = _make_client(_scoreboard([]))
        client.get_teams.return_value = _teams_response("12", "13")
        mock_s3 = _make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            nfl_ingest._fetch_rosters(client)

        written_keys = {call.kwargs["Key"] for call in mock_s3.put_object.call_args_list}
        assert written_keys == {"nfl/roster/12.json", "nfl/roster/13.json"}

    def test_one_teams_failure_does_not_block_the_others(self):
        client = _make_client(_scoreboard([]))
        client.get_teams.return_value = _teams_response("12", "13")
        client.get_roster.side_effect = [Exception("boom"), {"team": {"id": "13"}, "timestamp": "2026-08-08T00:00:00Z", "athletes": []}]
        mock_s3 = _make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert fetched == 1
        assert failed == 1

    def test_no_teams_fetches_nothing(self):
        client = _make_client(_scoreboard([]))
        client.get_teams.return_value = _teams_response()
        mock_s3 = _make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched, failed = nfl_ingest._fetch_rosters(client)

        assert (fetched, failed) == (0, 0)
        client.get_roster.assert_not_called()

    def test_lambda_handler_wires_roster_fetch_into_its_own_run(self):
        board = _scoreboard([_completed_event("1")])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)
        mock_client.get_teams.return_value = _teams_response("12", "13")

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["rosters_fetched"] == 2
        assert result["rosters_failed"] == 0
