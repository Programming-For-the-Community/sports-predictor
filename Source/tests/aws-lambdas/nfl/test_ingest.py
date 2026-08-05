"""
Unit tests for the NFL ingest Lambda handler.

All AWS and ESPN calls are mocked -- these tests run without credentials
and verify handler logic: event routing, idempotency, error resilience,
and return-value counts.

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
import nfl_ingest
from datetime import date
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
    return {
        "week": {"number": week},
        "season": {"year": season_year, "type": season_type},
        "events": events,
    }


def _make_s3(existing_keys: set | None = None):
    """Return a mock S3 client where head_object raises 404 for unknown keys."""
    mock_s3 = MagicMock()
    existing = existing_keys or set()

    def _head(**kwargs):
        if kwargs.get("Key") in existing:
            return {}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    mock_s3.head_object.side_effect = _head
    return mock_s3


def _make_client(scoreboard: dict, summary: dict | None = None):
    mock = MagicMock()
    mock.get_scoreboard_for_date.return_value = scoreboard
    mock.get_scoreboard.return_value = scoreboard
    mock.get_summary.return_value = summary or {"header": {}, "boxscore": {}}
    mock.get_depth_chart.return_value = {"positions": {}}
    return mock


def _make_core_client():
    """Sensible empty-but-well-formed defaults -- most tests here aren't
    exercising enrichment at all (their fixture events have no
    "competitions" key, so _home_away_team_ids returns None and the
    per-team fetch loop never runs), they just need lambda_handler's
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

        assert result == {"processed": 0, "skipped": 0, "failed": 0}
        mock_client.get_scoreboard.assert_not_called()
        mock_client.get_scoreboard_for_date.assert_not_called()
        mock_s3.put_object.assert_not_called()

    def test_skips_preseason_when_auto_detected(self):
        board = _scoreboard([_completed_event("1")], season_type=1)
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0}
        mock_client.get_summary.assert_not_called()
        mock_s3.put_object.assert_not_called()

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

        assert result == {"processed": 1, "skipped": 2, "failed": 0}

    def test_returns_empty_counts_for_empty_scoreboard(self):
        board = _scoreboard([])
        mock_s3 = _make_s3()
        mock_client = _make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=_make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {"processed": 0, "skipped": 0, "failed": 0}
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


def _competition_event(event_id: str, home_id: str, away_id: str) -> dict:
    return {
        "id": event_id,
        "status": {"type": {"completed": False}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id}},
                {"homeAway": "away", "team": {"id": away_id}},
            ],
        }],
    }


class TestHomeAwayTeamIds:
    def test_extracts_home_and_away_ids(self):
        event = _competition_event("1", "12", "24")
        assert nfl_ingest._home_away_team_ids(event) == ("12", "24")

    def test_none_for_event_with_no_competitions(self):
        assert nfl_ingest._home_away_team_ids({"id": "1"}) is None

    def test_none_for_event_missing_a_side(self):
        event = {"id": "1", "competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "12"}}]}]}
        assert nfl_ingest._home_away_team_ids(event) is None


class TestFilterDepthChart:
    def test_keeps_only_qb_rb_wr_positions(self):
        raw = {
            "positions": {
                "qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]},
                "lde": {"position": {"abbreviation": "LDE"}, "athletes": [{"id": "2"}]},
                "wr": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "3"}]},
            },
        }
        result = nfl_ingest._filter_depth_chart(raw)
        assert set(result.keys()) == {"qb", "wr"}

    def test_empty_when_no_positions_key(self):
        assert nfl_ingest._filter_depth_chart({}) == {}

    def test_trims_each_athlete_down_to_just_id(self):
        # Confirmed live: ESPN's real athlete objects carry ~289KB/team of
        # link metadata (player card, stats, splits, game log, news, bio,
        # each with web + sportscenter:// variants) this project never
        # reads -- only "id" may survive into what gets stored.
        raw = {
            "positions": {
                "qb": {
                    "position": {"abbreviation": "QB", "name": "Quarterback", "id": "8"},
                    "athletes": [{
                        "id": "1", "uid": "s:20~l:28~a:1", "guid": "abc",
                        "links": [{"rel": ["playercard"], "href": "https://espn.com/..."}],
                    }],
                },
            },
        }

        result = nfl_ingest._filter_depth_chart(raw)

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}

    def test_athlete_missing_id_is_dropped(self):
        raw = {"positions": {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"noId": "x"}]}}}

        result = nfl_ingest._filter_depth_chart(raw)

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": []}}


class TestEnrichEvents:
    def test_attaches_coach_injuries_and_depth_chart_to_home_and_away(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.side_effect = lambda team_id: {
            "positions": {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": f"qb-{team_id}"}]}},
        }
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {
            "12": {"coach_id": "1", "coach_name": "Andy Reid", "experience": 27, "season_win_pct": 0.7},
        }
        core_client.get_team_injuries.side_effect = lambda team_id: [{"entity_id": f"p-{team_id}", "status": "Out"}]

        nfl_ingest._enrich_events(events, 2025, nfl_client, core_client)

        event = events[0]
        assert event["home_coach"] == {"coach_id": "1", "coach_name": "Andy Reid", "experience": 27, "season_win_pct": 0.7}
        assert event["away_coach"] is None  # team 24 has no entry in get_season_coaches' return value
        assert event["home_injuries"] == [{"entity_id": "p-12", "status": "Out"}]
        assert event["away_injuries"] == [{"entity_id": "p-24", "status": "Out"}]
        assert event["home_depth_chart"] == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "qb-12"}]}}

    def test_malformed_event_is_skipped_without_raising(self):
        events = [{"id": "1"}]  # no competitions -- _home_away_team_ids returns None
        nfl_client = MagicMock()
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}

        nfl_ingest._enrich_events(events, 2025, nfl_client, core_client)  # must not raise

        assert "home_coach" not in events[0]
        core_client.get_team_injuries.assert_not_called()

    def test_coach_fetch_failure_omits_coach_fields_without_raising(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = {"positions": {}}
        core_client = MagicMock()
        core_client.get_season_coaches.side_effect = Exception("ESPN 500")
        core_client.get_team_injuries.return_value = []

        nfl_ingest._enrich_events(events, 2025, nfl_client, core_client)  # must not raise

        assert events[0]["home_coach"] is None
        assert events[0]["away_coach"] is None

    def test_injury_fetch_failure_for_one_team_does_not_block_the_other(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = {"positions": {}}
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}

        def flaky_injuries(team_id):
            if team_id == "12":
                raise Exception("ESPN timeout")
            return [{"entity_id": "p-24", "status": "Questionable"}]

        core_client.get_team_injuries.side_effect = flaky_injuries

        nfl_ingest._enrich_events(events, 2025, nfl_client, core_client)  # must not raise

        assert events[0]["home_injuries"] is None
        assert events[0]["away_injuries"] == [{"entity_id": "p-24", "status": "Questionable"}]

    def test_depth_chart_fetch_failure_omits_field_without_raising(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.side_effect = Exception("ESPN 500")
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}
        core_client.get_team_injuries.return_value = []

        nfl_ingest._enrich_events(events, 2025, nfl_client, core_client)  # must not raise

        assert events[0]["home_depth_chart"] is None
        assert events[0]["away_depth_chart"] is None


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