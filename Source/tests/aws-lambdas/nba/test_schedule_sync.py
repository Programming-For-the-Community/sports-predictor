"""
Unit tests for the NBA schedule-sync Lambda handler: the full-season
lookahead loop, the idempotent already-synced skip, the preseason skip,
and per-date failure isolation. All AWS and ESPN calls are mocked.

The nba_schedule_sync module is registered in sys.modules by conftest.py.
"""
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import nba_schedule_sync


def _event(season_type=2):
    return {"season": {"year": 2026, "type": season_type}}


def _scoreboard(events):
    return {"events": events}


def _s3_with_no_existing_objects():
    """head_object always raises (nothing already synced) -- the common
    case these tests exercise unless a test explicitly overrides it."""
    mock_s3 = MagicMock()
    mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    return mock_s3


class TestLambdaHandler:
    def test_syncs_one_call_per_lookahead_day_when_nothing_already_synced(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert mock_client.get_scoreboard_for_date.call_count == nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS
        assert result["synced"] == nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS

    def test_a_date_already_synced_beyond_the_refresh_window_is_skipped_without_calling_espn(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # every date "already exists"

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        # Only the refresh window's own dates call ESPN -- everything
        # beyond it stays skipped, same as the old always-skip behavior.
        assert mock_client.get_scoreboard_for_date.call_count == nba_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        assert result["refreshed"] == nba_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        assert result["skipped"] == (
            nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS - nba_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        )
        assert result["synced"] == 0

    def test_a_date_already_synced_inside_the_refresh_window_is_refetched_and_overwritten(self):
        # Regression for a real complaint: a date once written was never
        # re-fetched at all, so a rescheduled game within this window
        # would silently never self-correct until the day it's played.
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2)])
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # every date "already exists"

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert result["refreshed"] == nba_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        assert mock_s3.put_object.call_count == nba_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS

    def test_preseason_date_is_skipped_not_synced(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=1)])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert result["synced"] == 0
        assert result["skipped"] == nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS
        mock_s3.put_object.assert_not_called()

    def test_one_dates_failure_does_not_block_the_rest(self):
        mock_client = MagicMock()
        responses = [Exception("ESPN timeout")] + [
            _scoreboard([]) for _ in range(nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS - 1)
        ]
        mock_client.get_scoreboard_for_date.side_effect = responses
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            result = nba_schedule_sync.lambda_handler({}, None)

        assert result["failed"] == 1
        assert result["synced"] == nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS - 1

    def test_regular_season_date_is_written_under_its_own_date_key(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2)])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            nba_schedule_sync.lambda_handler({}, None)

        written_keys = {c.kwargs["Key"] for c in mock_s3.put_object.call_args_list}
        assert len(written_keys) == nba_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS
        assert all(k.startswith("nba/scoreboard/") and k.endswith(".json") for k in written_keys)

    def test_a_failed_dates_own_object_is_never_written_so_tomorrows_run_retries_it(self):
        # The idempotent skip above is keyed on the object actually existing
        # in S3 -- a date whose fetch failed never reaches _put_json, so it
        # stays un-synced and gets retried on the next scheduled run, not
        # silently skipped forever the way an already-successful date is.
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.side_effect = Exception("ESPN timeout")
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(nba_schedule_sync, "NBAClient", return_value=mock_client), \
             patch.object(nba_schedule_sync, "_s3", mock_s3):
            nba_schedule_sync.lambda_handler({}, None)

        mock_s3.put_object.assert_not_called()
