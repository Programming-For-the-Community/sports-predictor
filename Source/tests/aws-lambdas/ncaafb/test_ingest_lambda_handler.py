"""
Unit tests for the NCAAFB ingest Lambda handler's core flow: season/week
resolution (explicit override vs. calendar auto-detection), the
teams/coaches cache refresh, and the completed-games gate on box-score
fetching. All AWS and CFBD calls are mocked.

Coach/ranking enrichment's own lookup logic is covered separately in
test_ingest_enrichment.py; this file patches enrich_games out entirely
via mock.

The ncaafb_ingest module is registered in sys.modules by conftest.py,
which also sets RAW_BUCKET_NAME before the module is imported.
"""
from unittest.mock import MagicMock, patch

import ncaafb_ingest


def _game(game_id="1", home_id="2", away_id="52", completed=False, start_date="2025-09-28T20:25:00.000Z"):
    return {
        "id": game_id, "homeId": home_id, "awayId": away_id,
        "completed": completed, "startDate": start_date,
    }


def _calendar_week(season_type="regular", week=4, first_game_start="2025-09-25T00:00:00.000Z"):
    return {"seasonType": season_type, "week": week, "firstGameStart": first_game_start}


class TestLambdaHandlerWeekResolution:
    def test_uses_explicit_season_week_type_without_calling_calendar(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", MagicMock()), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2024, "week": 3, "season_type": "postseason"}, None)

        mock_client.get_calendar.assert_not_called()
        mock_client.get_games.assert_called_once_with(2024, week=3, season_type="postseason")

    def test_auto_detects_most_recently_started_week(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = [
            _calendar_week(week=3, first_game_start="2025-09-18T00:00:00.000Z"),
            _calendar_week(week=4, first_game_start="2025-09-25T00:00:00.000Z"),
            _calendar_week(week=5, first_game_start="2099-01-01T00:00:00.000Z"),  # not started yet
        ]

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", MagicMock()), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({}, None)

        mock_client.get_games.assert_called_once()
        assert mock_client.get_games.call_args.kwargs["week"] == 4

    def test_returns_early_when_no_week_has_started_yet(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = [
            _calendar_week(week=1, first_game_start="2099-01-01T00:00:00.000Z"),
        ]

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["processed"] == 0
        assert result["failed"] == 0
        mock_client.get_games.assert_not_called()

    def test_partial_override_still_triggers_auto_detection(self):
        # All three of season/week/season_type must be given together --
        # season alone isn't a full manual override.
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = [_calendar_week(week=4)]

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", MagicMock()), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2025}, None)

        mock_client.get_calendar.assert_called_once()


class TestLambdaHandlerCacheRefresh:
    def test_teams_cached_true_on_success(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["teams_cached"] is True

    def test_teams_cached_false_on_failure_does_not_crash_the_run(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams", side_effect=Exception("CFBD timeout")), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["teams_cached"] is False

    def test_coaches_cached_false_on_failure_does_not_crash_the_run(self):
        mock_client = MagicMock()
        mock_client.get_calendar.return_value = []

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches", side_effect=Exception("CFBD timeout")):
            result = ncaafb_ingest.lambda_handler({}, None)

        assert result["coaches_cached"] is False


class TestLambdaHandlerBoxScores:
    def test_no_completed_games_skips_box_score_fetch_entirely(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(completed=False)]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            result = ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        assert result["processed"] == 0
        mock_client.get_game_player_stats.assert_not_called()
        mock_client.get_game_team_stats.assert_not_called()

    def test_completed_game_triggers_both_player_and_team_box_score_fetch(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(game_id="1", completed=True)]
        mock_client.get_game_player_stats.return_value = [{"id": "1"}]
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            result = ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        assert result["processed"] == 2
        assert result["failed"] == 0
        mock_client.get_game_player_stats.assert_called_once_with(2025, 4, "regular")
        mock_client.get_game_team_stats.assert_called_once_with(2025, 4, "regular")

    def test_box_scores_are_annotated_with_home_away_and_event_date(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [
            _game(game_id="1", home_id="2", away_id="52", completed=True, start_date="2025-09-28T20:25:00.000Z"),
        ]
        mock_client.get_game_player_stats.return_value = [{"id": "1"}]
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        written = {c.kwargs["Key"]: c.kwargs["Body"] for c in mock_s3.put_object.call_args_list}
        boxscore_body = written["ncaafb/boxscore/2025/regular/4.json"]
        assert b'"home_id": "2"' in boxscore_body
        assert b'"away_id": "52"' in boxscore_body
        assert b'"event_date": "2025-09-28"' in boxscore_body

    def test_player_box_score_failure_does_not_block_team_box_score(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(completed=True)]
        mock_client.get_game_player_stats.side_effect = Exception("CFBD timeout")
        mock_client.get_game_team_stats.return_value = [{"id": "1"}]
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games"):
            result = ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        assert result["processed"] == 1
        assert result["failed"] == 1
        mock_client.get_game_team_stats.assert_called_once()

    def test_games_are_written_before_box_scores_are_considered(self):
        mock_client = MagicMock()
        mock_client.get_games.return_value = [_game(completed=True)]
        mock_client.get_game_player_stats.return_value = []
        mock_client.get_game_team_stats.return_value = []
        mock_s3 = MagicMock()

        with patch.object(ncaafb_ingest, "CFBDClient", return_value=mock_client), \
             patch.object(ncaafb_ingest, "_s3", mock_s3), \
             patch.object(ncaafb_ingest, "get_cached_teams"), \
             patch.object(ncaafb_ingest.enrichment, "get_cached_coaches"), \
             patch.object(ncaafb_ingest.enrichment, "enrich_games") as mock_enrich:
            ncaafb_ingest.lambda_handler({"season": 2025, "week": 4, "season_type": "regular"}, None)

        written_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
        assert "ncaafb/games/2025/regular/4.json" in written_keys
        mock_enrich.assert_called_once()


class TestCurrentNcaafbSeason:
    def test_september_resolves_to_this_year(self):
        from datetime import date
        assert ncaafb_ingest._current_ncaafb_season(date(2025, 9, 1)) == 2025

    def test_january_resolves_to_last_year(self):
        from datetime import date
        assert ncaafb_ingest._current_ncaafb_season(date(2026, 1, 15)) == 2025

    def test_february_resolves_to_this_year(self):
        # Championship is done by late January -- Feb already means the
        # UPCOMING season.
        from datetime import date
        assert ncaafb_ingest._current_ncaafb_season(date(2026, 2, 1)) == 2026
