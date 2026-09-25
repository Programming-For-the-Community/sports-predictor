"""
Unit tests for the NFL ingest Lambda handler's core scoreboard/box-score
flow: event routing (explicit vs. auto-detected week), idempotency
(skipping already-ingested box scores), error resilience (one game's
failure doesn't block the rest), preseason skipping, and aggregate return
counts. Shared fixtures live in _ingest_test_helpers.py (not
test_-prefixed, so pytest never collects it as a test module itself).

All AWS and ESPN calls are mocked -- these tests run without credentials.
Coach/injury/depth-chart enrichment itself is covered separately in
test_enrichment.py.

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
from unittest.mock import patch

import nfl_ingest

from _ingest_test_helpers import (
    SEASON_YEAR,
    completed_event,
    incomplete_event,
    make_client,
    make_core_client,
    make_s3,
    scoreboard,
    teams_response,
)


class TestIngestLambdaHandler:
    def test_processes_completed_events(self):
        board = scoreboard([completed_event("123")])
        mock_s3 = make_s3()
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["failed"] == 0
        mock_client.get_summary.assert_called_once_with("123")
        mock_s3.put_object.assert_called()

    def test_skips_incomplete_events(self):
        board = scoreboard([completed_event("1"), incomplete_event("2")])
        mock_s3 = make_s3()
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["skipped"] == 1
        mock_client.get_summary.assert_called_once_with("1")

    def test_skips_events_already_in_s3(self):
        board = scoreboard([completed_event("123")])
        # Pre-mark the box score key as already present
        existing_key = f"nfl/boxscore/{SEASON_YEAR}/123.json"
        mock_s3 = make_s3(existing_keys={existing_key})
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 0
        assert result["skipped"] == 1
        mock_client.get_summary.assert_not_called()

    def test_uses_explicit_week_from_event_payload(self):
        board = scoreboard([])
        mock_s3 = make_s3()
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            nfl_ingest.lambda_handler({"season": 2024, "season_type": 2, "week": 3}, None)

        mock_client.get_scoreboard.assert_called_once_with(2024, 2, 3)
        mock_client.get_scoreboard_for_date.assert_not_called()

    def test_auto_detects_week_when_not_in_payload(self):
        board = scoreboard([], week=7)
        mock_s3 = make_s3()
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            nfl_ingest.lambda_handler({}, None)

        # Exact date value is covered by test_ingest_helpers.py's own
        # TestMostRecentSunday -- here we only need to confirm the
        # date-based lookup is what finds the week; the week's games then
        # come from the explicit-week call (the date-based response only
        # holds that one day's games).
        mock_client.get_scoreboard_for_date.assert_called_once()
        mock_client.get_scoreboard.assert_any_call(SEASON_YEAR, 2, 7)

    def test_auto_detect_ingests_thursday_and_monday_games_missing_from_date_scoreboard(self):
        # Real ESPN behavior: dates=<Sunday> returns only Sunday's games.
        # The Thursday/Monday games exist only in the full-week response.
        sunday_only = scoreboard([completed_event("sun")], week=2)
        full_week = scoreboard([completed_event("thu"), completed_event("sun"), completed_event("mon")], week=2)
        mock_s3 = make_s3()
        mock_client = make_client(full_week)
        mock_client.get_scoreboard_for_date.return_value = sunday_only

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 3
        fetched = {call.args[0] for call in mock_client.get_summary.call_args_list}
        assert fetched == {"thu", "sun", "mon"}

    def test_auto_detect_also_ingests_next_week_so_thursday_game_finalizes(self):
        # Friday-after-Thursday-night: most recent Sunday is still last
        # week's, but the new week's Thursday game is already final.
        this_week = scoreboard([completed_event("old")], week=2)
        next_week = scoreboard([completed_event("thu3"), incomplete_event("sun3")], week=3)
        mock_s3 = make_s3()
        mock_client = make_client(this_week, boards_by_week={(2, 3): next_week})

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 2
        assert result["skipped"] == 1
        keys = {call.kwargs["Key"] for call in mock_s3.put_object.call_args_list}
        assert f"nfl/scoreboard/{SEASON_YEAR}/2/3.json" in keys

    def test_auto_detect_falls_through_to_postseason_after_final_regular_season_week(self):
        week_18 = scoreboard([completed_event("r18")], week=18)
        wild_card = scoreboard([completed_event("wc1")], week=1, season_type=3)
        mock_s3 = make_s3()
        mock_client = make_client(week_18, boards_by_week={(3, 1): wild_card})

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 2
        mock_client.get_scoreboard.assert_any_call(SEASON_YEAR, 3, 1)

    def test_explicit_week_does_not_ingest_next_week(self):
        board = scoreboard([completed_event("1")], week=3)
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", make_s3()),              patch.object(nfl_ingest, "NFLClient", return_value=mock_client),              patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            nfl_ingest.lambda_handler({"season": SEASON_YEAR, "season_type": 2, "week": 3}, None)

        mock_client.get_scoreboard.assert_called_once_with(SEASON_YEAR, 2, 3)

    def test_empty_next_week_writes_no_scoreboard(self):
        mock_s3 = make_s3()
        mock_client = make_client(scoreboard([completed_event("1")], week=5))

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            nfl_ingest.lambda_handler({}, None)

        keys = {call.kwargs["Key"] for call in mock_s3.put_object.call_args_list}
        assert f"nfl/scoreboard/{SEASON_YEAR}/2/6.json" not in keys

    def test_skips_preseason_given_explicitly(self):
        mock_s3 = make_s3()
        mock_client = make_client(scoreboard([]))

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({"season": 2025, "season_type": 1, "week": 1}, None)

        assert result == {
            "processed": 0, "skipped": 0, "failed": 0,
            "rosters_fetched": 0, "rosters_failed": 0,
            "depth_charts_fetched": 0, "depth_charts_failed": 0,
            "coaches_fetched": True,
        }
        mock_client.get_scoreboard.assert_not_called()
        mock_client.get_scoreboard_for_date.assert_not_called()

    def test_skips_preseason_when_auto_detected(self):
        board = scoreboard([completed_event("1")], season_type=1)
        mock_s3 = make_s3()
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {
            "processed": 0, "skipped": 0, "failed": 0,
            "rosters_fetched": 0, "rosters_failed": 0,
            "depth_charts_fetched": 0, "depth_charts_failed": 0,
            "coaches_fetched": True,
        }
        mock_client.get_summary.assert_not_called()

    def test_fetches_rosters_even_during_preseason(self):
        # The one exception to "preseason isn't ingested" -- roster
        # fetching runs before that check.
        mock_s3 = make_s3()
        mock_client = make_client(scoreboard([]))
        mock_client.get_teams.return_value = teams_response("12", "13")

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({"season": 2025, "season_type": 1, "week": 1}, None)

        assert result["rosters_fetched"] == 2
        mock_client.get_roster.assert_any_call("12")
        mock_client.get_roster.assert_any_call("13")

    def test_fetches_depth_charts_even_during_preseason(self):
        # Same exception as roster fetching above -- depth charts get the
        # same unconditional, every-team treatment now, not gated behind
        # a week's scoreboard or the preseason check.
        mock_s3 = make_s3()
        mock_client = make_client(scoreboard([]))
        mock_client.get_teams.return_value = teams_response("12", "13")

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({"season": 2025, "season_type": 1, "week": 1}, None)

        assert result["depth_charts_fetched"] == 2
        mock_client.get_depth_chart.assert_any_call("12")
        mock_client.get_depth_chart.assert_any_call("13")

    def test_continues_after_individual_game_failure(self):
        board = scoreboard([completed_event("1"), completed_event("2")])
        mock_s3 = make_s3()
        mock_client = make_client(board)
        mock_client.get_summary.side_effect = [Exception("ESPN timeout"), {"header": {}, "boxscore": {}}]

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1

    def test_returns_correct_aggregate_counts(self):
        board = scoreboard([
            completed_event("1"),  # processed
            incomplete_event("2"),  # skipped (not done)
            completed_event("3"),  # skipped (already in S3)
        ])
        existing_key = f"nfl/boxscore/{SEASON_YEAR}/3.json"
        mock_s3 = make_s3(existing_keys={existing_key})
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {
            "processed": 1, "skipped": 2, "failed": 0,
            "rosters_fetched": 0, "rosters_failed": 0,
            "depth_charts_fetched": 0, "depth_charts_failed": 0,
            "coaches_fetched": True,
        }

    def test_returns_empty_counts_for_empty_scoreboard(self):
        board = scoreboard([])
        mock_s3 = make_s3()
        mock_client = make_client(board)

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=make_core_client()):
            result = nfl_ingest.lambda_handler({}, None)

        assert result == {
            "processed": 0, "skipped": 0, "failed": 0,
            "rosters_fetched": 0, "rosters_failed": 0,
            "depth_charts_fetched": 0, "depth_charts_failed": 0,
            "coaches_fetched": True,
        }
        mock_client.get_summary.assert_not_called()
