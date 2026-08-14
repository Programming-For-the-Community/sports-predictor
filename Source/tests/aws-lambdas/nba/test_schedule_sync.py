"""
Unit tests for the NBA schedule-sync Lambda handler: the lookahead-window
loop, the preseason skip, and per-date failure isolation. All AWS and
ESPN calls are mocked.

The nba_schedule_sync module is registered in sys.modules by conftest.py.
"""
from unittest.mock import MagicMock, patch

import nba_schedule_sync


def _event(season_type=2):
    return {"season": {"year": 2026, "type": season_type}}


def _scoreboard(events):
    return {"events": events}


class TestLambdaHandler:
    def test_syncs_one_call_per_lookahead_day(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = MagicMock()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert mock_client.get_scoreboard_for_date.call_count == nba_schedule_sync.SCHEDULE_SYNC_LOOKAHEAD_DAYS
        assert result["synced"] == nba_schedule_sync.SCHEDULE_SYNC_LOOKAHEAD_DAYS

    def test_preseason_date_is_skipped_not_synced(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=1)])
        mock_s3 = MagicMock()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert result["synced"] == 0
        assert result["skipped"] == nba_schedule_sync.SCHEDULE_SYNC_LOOKAHEAD_DAYS
        mock_s3.put_object.assert_not_called()

    def test_one_dates_failure_does_not_block_the_rest(self):
        mock_client = MagicMock()
        responses = [Exception("ESPN timeout")] + [_scoreboard([]) for _ in range(nba_schedule_sync.SCHEDULE_SYNC_LOOKAHEAD_DAYS - 1)]
        mock_client.get_scoreboard_for_date.side_effect = responses
        mock_s3 = MagicMock()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert result["failed"] == 1
        assert result["synced"] == nba_schedule_sync.SCHEDULE_SYNC_LOOKAHEAD_DAYS - 1

    def test_regular_season_date_is_written_under_its_own_date_key(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2)])
        mock_s3 = MagicMock()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            nba_schedule_sync.lambda_handler({}, None)

        written_keys = {c.kwargs["Key"] for c in mock_s3.put_object.call_args_list}
        assert len(written_keys) == nba_schedule_sync.SCHEDULE_SYNC_LOOKAHEAD_DAYS
        assert all(k.startswith("nba/scoreboard/") and k.endswith(".json") for k in written_keys)
