"""
Unit tests for the PGA schedule-sync Lambda handler: the one-call season-
calendar discovery, the idempotent already-synced skip, the startDate-
based refresh window, and per-tournament failure isolation. All AWS and
ESPN calls are mocked.

The pga_schedule_sync module is registered in sys.modules by conftest.py.
"""
from datetime import date
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import pga_schedule_sync


def _calendar_entry(event_id="1", label="Some Championship", start_date="2026-08-20T04:00Z"):
    return {"id": event_id, "label": label, "startDate": start_date}


def _scoreboard(calendar, season_year=2026):
    return {"leagues": [{"season": {"year": season_year}, "calendar": calendar}]}


def _s3_with_no_existing_objects():
    mock_s3 = MagicMock()
    mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    return mock_s3


class TestLambdaHandler:
    def test_fetches_the_calendar_with_a_single_scoreboard_call(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(pga_schedule_sync, "PGAClient", return_value=mock_client), \
             patch.object(pga_schedule_sync, "_s3", mock_s3), \
             patch("pga_schedule_sync.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            pga_schedule_sync.lambda_handler({}, None)

        mock_client.get_scoreboard_for_date.assert_called_once_with("20260824")

    def test_syncs_one_leaderboard_call_per_new_tournament(self):
        mock_client = MagicMock()
        calendar = [_calendar_entry(event_id="1"), _calendar_entry(event_id="2")]
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(calendar)
        mock_client.get_leaderboard.return_value = {"events": [{"id": "x"}]}
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(pga_schedule_sync, "PGAClient", return_value=mock_client), \
             patch.object(pga_schedule_sync, "_s3", mock_s3), \
             patch("pga_schedule_sync.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            result = pga_schedule_sync.lambda_handler({}, None)

        assert mock_client.get_leaderboard.call_count == 2
        assert result["synced"] == 2
        written_keys = {c.kwargs["Key"] for c in mock_s3.put_object.call_args_list}
        assert written_keys == {"pga/leaderboard/2026/1.json", "pga/leaderboard/2026/2.json"}

    def test_a_tournament_already_synced_outside_the_refresh_window_is_skipped(self):
        mock_client = MagicMock()
        # startDate far from "today" (2026-08-24) -- outside the window.
        calendar = [_calendar_entry(event_id="1", start_date="2026-01-08T08:00Z")]
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(calendar)
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # already exists

        with patch.object(pga_schedule_sync, "PGAClient", return_value=mock_client), \
             patch.object(pga_schedule_sync, "_s3", mock_s3), \
             patch("pga_schedule_sync.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            result = pga_schedule_sync.lambda_handler({}, None)

        assert result["skipped"] == 1
        assert result["synced"] == 0
        assert result["refreshed"] == 0
        mock_client.get_leaderboard.assert_not_called()

    def test_a_tournament_already_synced_inside_the_refresh_window_is_refetched(self):
        mock_client = MagicMock()
        calendar = [_calendar_entry(event_id="1", start_date="2026-08-20T04:00Z")]
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(calendar)
        mock_client.get_leaderboard.return_value = {"events": [{"id": "1"}]}
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # already exists

        with patch.object(pga_schedule_sync, "PGAClient", return_value=mock_client), \
             patch.object(pga_schedule_sync, "_s3", mock_s3), \
             patch("pga_schedule_sync.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            result = pga_schedule_sync.lambda_handler({}, None)

        assert result["refreshed"] == 1
        assert result["synced"] == 0
        mock_client.get_leaderboard.assert_called_once_with("1")

    def test_one_tournaments_fetch_failure_does_not_block_the_others(self):
        mock_client = MagicMock()
        calendar = [_calendar_entry(event_id="1"), _calendar_entry(event_id="2")]
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(calendar)
        mock_client.get_leaderboard.side_effect = [Exception("ESPN timeout"), {"events": [{"id": "2"}]}]
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(pga_schedule_sync, "PGAClient", return_value=mock_client), \
             patch.object(pga_schedule_sync, "_s3", mock_s3), \
             patch("pga_schedule_sync.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            result = pga_schedule_sync.lambda_handler({}, None)

        assert result["failed"] == 1
        assert result["synced"] == 1

    def test_a_failed_tournaments_object_is_never_written(self):
        mock_client = MagicMock()
        calendar = [_calendar_entry(event_id="1")]
        mock_client.get_scoreboard_for_date.return_value = _scoreboard(calendar)
        mock_client.get_leaderboard.side_effect = Exception("ESPN timeout")
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(pga_schedule_sync, "PGAClient", return_value=mock_client), \
             patch.object(pga_schedule_sync, "_s3", mock_s3), \
             patch("pga_schedule_sync.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 24)
            pga_schedule_sync.lambda_handler({}, None)

        mock_s3.put_object.assert_not_called()
