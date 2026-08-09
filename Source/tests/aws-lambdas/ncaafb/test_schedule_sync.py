"""
Unit tests for the NCAAFB schedule-sync Lambda handler. All AWS and CFBD
calls are mocked.

The ncaafb_schedule_sync module is registered in sys.modules by
conftest.py, which also sets RAW_BUCKET_NAME before the module is
imported.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import ncaafb_schedule_sync


def _make_client(games=None):
    mock = MagicMock()
    mock.get_games.return_value = games if games is not None else []
    return mock


class TestLambdaHandler:
    def test_syncs_every_week_of_both_season_types(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"):
            result = ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        # 17 regular-season weeks (0-16) + 5 postseason weeks.
        assert mock_client.get_games.call_count == 22
        assert result == {"season": 2026, "synced": 22, "failed": 0}

    def test_uses_one_shared_client_for_the_whole_run(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()
        client_factory = MagicMock(return_value=mock_client)

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", client_factory), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"):
            ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        client_factory.assert_called_once()

    def test_calls_regular_season_weeks_0_through_16(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"):
            ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        regular_calls = [c for c in mock_client.get_games.call_args_list if c.kwargs["season_type"] == "regular"]
        assert sorted(c.kwargs["week"] for c in regular_calls) == list(range(0, 17))

    def test_calls_postseason_weeks_1_through_5(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"):
            ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        postseason_calls = [c for c in mock_client.get_games.call_args_list if c.kwargs["season_type"] == "postseason"]
        assert sorted(c.kwargs["week"] for c in postseason_calls) == list(range(1, 6))

    def test_writes_each_week_to_its_own_games_key(self):
        mock_s3 = MagicMock()
        mock_client = _make_client([{"id": "1"}])

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"):
            ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaafb/games/2026/regular/1.json" in written_keys
        assert "ncaafb/games/2026/postseason/1.json" in written_keys

    def test_attaches_venue_indoor_to_every_week(self):
        mock_s3 = MagicMock()
        mock_client = _make_client([{"id": "1"}])

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor") as mock_attach:
            ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        assert mock_attach.call_count == 22

    def test_never_fetches_coach_or_ranking_data(self):
        # Full enrichment (coach/rankings) stays ingest-only -- confirm
        # there's no CFBDClient.get_coaches/get_rankings call anywhere in
        # this module, and no import of ingest's own enrichment module.
        assert not hasattr(ncaafb_schedule_sync, "enrichment")

    def test_uses_explicit_season_from_event_payload(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"), \
             patch.object(ncaafb_schedule_sync, "_current_ncaafb_season") as mock_season:
            ncaafb_schedule_sync.lambda_handler({"season": 2024}, None)

        mock_season.assert_not_called()
        assert mock_client.get_games.call_args_list[0].args[0] == 2024

    def test_resolves_season_from_calendar_when_omitted(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"), \
             patch.object(ncaafb_schedule_sync, "_current_ncaafb_season", return_value=2027):
            result = ncaafb_schedule_sync.lambda_handler({}, None)

        assert result["season"] == 2027
        assert mock_client.get_games.call_args_list[0].args[0] == 2027

    def test_one_weeks_failure_does_not_block_the_rest(self):
        mock_s3 = MagicMock()
        mock_client = _make_client()
        mock_client.get_games.side_effect = [Exception("CFBD timeout"), *([[]] * 21)]

        with patch.object(ncaafb_schedule_sync, "_s3", mock_s3), \
             patch.object(ncaafb_schedule_sync, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_schedule_sync, "attach_venue_indoor"):
            result = ncaafb_schedule_sync.lambda_handler({"season": 2026}, None)

        assert result == {"season": 2026, "synced": 21, "failed": 1}
        assert mock_client.get_games.call_count == 22


class TestCurrentNcaafbSeason:
    def test_september_resolves_to_this_year(self):
        assert ncaafb_schedule_sync._current_ncaafb_season(date(2026, 9, 1)) == 2026

    def test_january_resolves_to_last_year(self):
        assert ncaafb_schedule_sync._current_ncaafb_season(date(2027, 1, 15)) == 2026

    def test_february_resolves_to_this_year(self):
        assert ncaafb_schedule_sync._current_ncaafb_season(date(2027, 2, 1)) == 2027

    def test_defaults_to_actual_today_when_not_given(self):
        result = ncaafb_schedule_sync._current_ncaafb_season()
        assert isinstance(result, int)
