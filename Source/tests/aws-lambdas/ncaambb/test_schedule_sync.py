"""
Unit tests for the NCAA MBB schedule-sync Lambda handler: the full-season
lookahead loop, the idempotent already-synced skip, per-date failure
isolation, and the conference-membership cache refresh
(_sync_conference_membership/_current_ncaambb_season -- see handler.py's
own CONFERENCE MEMBERSHIP docstring section for why this Lambda, not
predict/season_projection.py, resolves it). All AWS and ESPN calls are
mocked.

Unlike NBA's own test_schedule_sync.py, there is no preseason-skip test
here -- confirmed live, 2026-08-19 (see handler.py's own docstring), NCAA
MBB's ESPN scoreboard has no preseason concept to skip.

The ncaambb_schedule_sync module is registered in sys.modules by
conftest.py.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import ncaambb_schedule_sync


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


class TestCurrentNcaambbSeason:
    def test_before_august_uses_the_current_calendar_year(self):
        assert ncaambb_schedule_sync._current_ncaambb_season(date(2026, 3, 15)) == 2026

    def test_august_or_later_uses_next_calendar_year(self):
        assert ncaambb_schedule_sync._current_ncaambb_season(date(2026, 8, 22)) == 2027

    def test_december_uses_next_calendar_year(self):
        assert ncaambb_schedule_sync._current_ncaambb_season(date(2026, 12, 1)) == 2027


class TestConferenceMembershipSync:
    def test_writes_the_resolved_membership_to_the_expected_key(self):
        mock_s3 = MagicMock()
        mock_core_client = MagicMock()

        with patch.object(ncaambb_schedule_sync, "NCAAMBBCoreClient", return_value=mock_core_client), \
             patch.object(ncaambb_schedule_sync, "resolve_conference_membership", return_value={"150": "ACC"}), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            ncaambb_schedule_sync._sync_conference_membership(2027)

        mock_s3.put_object.assert_called_once()
        call = mock_s3.put_object.call_args.kwargs
        assert call["Key"] == "ncaambb/conference-membership/2027.json"

    def test_a_failure_does_not_block_the_date_walk_loop(self):
        # lambda_handler's own try/except around this call -- a live ESPN
        # hiccup shouldn't cost the run its own schedule-sync date-walk.
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaambb_schedule_sync, "_sync_conference_membership", side_effect=Exception("ESPN unreachable")):
            result = ncaambb_schedule_sync.lambda_handler({}, None)

        assert result["synced"] == ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS


class TestLambdaHandler:
    @pytest.fixture(autouse=True)
    def stub_conference_membership_sync(self):
        """Every test below exercises the date-walk loop, not the
        conference-membership refresh -- stubbed out so none of them make
        a real NCAAMBBCoreClient call. Class-scoped (not module-level) so
        TestConferenceMembershipSync's own direct calls to
        _sync_conference_membership above aren't neutered by it too."""
        with patch.object(ncaambb_schedule_sync, "_sync_conference_membership"):
            yield

    def test_syncs_one_call_per_lookahead_day_when_nothing_already_synced(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            result = ncaambb_schedule_sync.lambda_handler({}, None)

        assert mock_client.get_scoreboard_for_date.call_count == ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS
        assert result["synced"] == ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS

    def test_a_date_already_synced_beyond_the_refresh_window_is_skipped_without_calling_espn(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([])
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # every date "already exists"

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            result = ncaambb_schedule_sync.lambda_handler({}, None)

        # Only the refresh window's own dates call ESPN -- everything
        # beyond it stays skipped.
        assert mock_client.get_scoreboard_for_date.call_count == ncaambb_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        assert result["refreshed"] == ncaambb_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        assert result["skipped"] == (
            ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS - ncaambb_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        )
        assert result["synced"] == 0

    def test_a_date_already_synced_inside_the_refresh_window_is_refetched_and_overwritten(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2)])
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {}  # every date "already exists"

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            result = ncaambb_schedule_sync.lambda_handler({}, None)

        assert result["refreshed"] == ncaambb_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        assert mock_s3.put_object.call_count == ncaambb_schedule_sync.SCHEDULE_SYNC_REFRESH_WINDOW_DAYS

    def test_one_dates_failure_does_not_block_the_rest(self):
        mock_client = MagicMock()
        responses = [Exception("ESPN timeout")] + [
            _scoreboard([]) for _ in range(ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS - 1)
        ]
        mock_client.get_scoreboard_for_date.side_effect = responses
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            result = ncaambb_schedule_sync.lambda_handler({}, None)

        assert result["failed"] == 1
        assert result["synced"] == ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS - 1

    def test_regular_season_date_is_written_under_its_own_date_key(self):
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.return_value = _scoreboard([_event(season_type=2)])
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            ncaambb_schedule_sync.lambda_handler({}, None)

        written_keys = {c.kwargs["Key"] for c in mock_s3.put_object.call_args_list}
        assert len(written_keys) == ncaambb_schedule_sync.SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS
        assert all(k.startswith("ncaambb/scoreboard/") and k.endswith(".json") for k in written_keys)

    def test_a_failed_dates_own_object_is_never_written_so_next_runs_retries_it(self):
        # The idempotent skip above is keyed on the object actually existing
        # in S3 -- a date whose fetch failed never reaches _put_json, so it
        # stays un-synced and gets retried on the next scheduled run, not
        # silently skipped forever the way an already-successful date is.
        mock_client = MagicMock()
        mock_client.get_scoreboard_for_date.side_effect = Exception("ESPN timeout")
        mock_s3 = _s3_with_no_existing_objects()

        with patch.object(ncaambb_schedule_sync, "NCAAMBBClient", return_value=mock_client), \
             patch.object(ncaambb_schedule_sync, "_s3", mock_s3):
            ncaambb_schedule_sync.lambda_handler({}, None)

        mock_s3.put_object.assert_not_called()
