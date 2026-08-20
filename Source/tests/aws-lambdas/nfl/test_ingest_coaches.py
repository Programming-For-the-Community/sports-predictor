"""
Unit tests for the NFL ingest Lambda's season-detection helper
(_current_nfl_season) and its daily, unconditional coach-cache refresh
(_fetch_coaches) -- same every-run cadence as rosters/depth charts,
seeding next season's coaches during the off-season instead of only
ever appearing as a side effect of enriching a specific week's events
(which never runs before Week 1).

The nfl_ingest module is registered in sys.modules by conftest.py, which
also sets RAW_BUCKET_NAME before the module is imported (it's read at
module level by the handler).
"""
from datetime import date
from unittest.mock import patch

import nfl_ingest

from _ingest_test_helpers import completed_event, make_client, make_core_client, make_s3, scoreboard


class TestCurrentNflSeason:
    def test_september_resolves_to_the_season_in_progress(self):
        assert nfl_ingest._current_nfl_season(date(2025, 9, 15)) == 2025

    def test_december_resolves_to_the_season_in_progress(self):
        assert nfl_ingest._current_nfl_season(date(2025, 12, 25)) == 2025

    def test_january_resolves_to_last_year_still_finishing_the_playoffs(self):
        assert nfl_ingest._current_nfl_season(date(2026, 1, 20)) == 2025

    def test_february_resolves_to_last_year_still_finishing_the_playoffs(self):
        assert nfl_ingest._current_nfl_season(date(2026, 2, 5)) == 2025

    def test_march_through_august_resolves_to_the_upcoming_season(self):
        assert nfl_ingest._current_nfl_season(date(2026, 8, 8)) == 2026
        assert nfl_ingest._current_nfl_season(date(2026, 3, 1)) == 2026


class TestFetchCoaches:
    def test_refreshes_the_coaches_cache_for_the_current_season(self):
        core_client = make_core_client()
        core_client.get_season_coaches.return_value = {"12": {"coach_id": "1"}}
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "_current_nfl_season", return_value=2026):
            fetched = nfl_ingest._fetch_coaches(core_client)

        assert fetched is True
        core_client.get_season_coaches.assert_called_once_with(2026)

    def test_a_fetch_failure_is_reported_not_raised(self):
        core_client = make_core_client()
        core_client.get_season_coaches.side_effect = Exception("ESPN down")
        mock_s3 = make_s3()

        with patch.object(nfl_ingest, "_s3", mock_s3):
            fetched = nfl_ingest._fetch_coaches(core_client)

        assert fetched is False

    def test_lambda_handler_fetches_coaches_even_before_any_regular_season_week_exists(self):
        # No week to auto-detect yet (auto-detection resolves preseason
        # and returns early), but coaches should still get fetched/cached
        # for the upcoming season regardless.
        mock_s3 = make_s3()
        mock_client = make_client(scoreboard([], season_type=1))  # preseason, auto-detected
        core_client = make_core_client()
        core_client.get_season_coaches.return_value = {"12": {"coach_id": "1"}}

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=core_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["coaches_fetched"] is True
        core_client.get_season_coaches.assert_called_once()

    def test_lambda_handler_wires_coach_fetch_into_its_own_run(self):
        board = scoreboard([completed_event("1")])
        mock_s3 = make_s3()
        mock_client = make_client(board)
        core_client = make_core_client()
        core_client.get_season_coaches.return_value = {"12": {"coach_id": "1"}}

        with patch.object(nfl_ingest, "_s3", mock_s3), \
             patch.object(nfl_ingest, "NFLClient", return_value=mock_client), \
             patch.object(nfl_ingest, "EspnCoreApiClient", return_value=core_client):
            result = nfl_ingest.lambda_handler({}, None)

        assert result["coaches_fetched"] is True
