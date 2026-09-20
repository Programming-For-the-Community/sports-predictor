"""
Unit tests for season_projection.py's own leaderboard-building
orchestration -- _leaderboards. Model loading/prediction itself is
covered by tests/library/ml/test_model_types.py; these tests patch
_project_stat_leaderboard directly to isolate _leaderboards' own
per-stat error handling.
"""
from unittest.mock import MagicMock, patch

import season_projection


def _season_inputs():
    return {"completed_event_keys": frozenset(), "games_remaining": {}, "team_next_event": {}}


def _empty_candidates():
    return {stat: set() for stat in season_projection.PLAYER_PROP_STATS}


class TestLeaderboards:
    def test_one_stats_model_failure_does_not_block_the_others(self):
        storage = MagicMock()
        storage.get_all_player_game_stats.return_value = []

        def fake_project(storage, s3, model_cache, season_inputs, stat, candidates, current_totals_by_stat, feature_row_cache, player_team):
            if stat == "defensive_sacks":
                # Mirrors a real failure -- a promoted model pickled under
                # a since-upgraded scikit-learn no longer unpickles cleanly.
                raise AttributeError("'SimpleImputer' object has no attribute '_fill_dtype'")
            return [{"entity_id": f"p-{stat}"}]

        with patch.object(season_projection, "_depth_chart_feature_rows", return_value=({}, _empty_candidates())), \
             patch.object(season_projection, "_fill_remaining_feature_rows"), \
             patch.object(season_projection, "_project_stat_leaderboard", side_effect=fake_project):
            result = season_projection._leaderboards(storage, MagicMock(), {}, _season_inputs())

        assert "defensive_sacks" not in result
        assert set(result.keys()) == set(season_projection.PLAYER_PROP_STATS) - {"defensive_sacks"}

    def test_every_stat_succeeding_returns_all_of_them(self):
        storage = MagicMock()
        storage.get_all_player_game_stats.return_value = []

        with patch.object(season_projection, "_depth_chart_feature_rows", return_value=({}, _empty_candidates())), \
             patch.object(season_projection, "_fill_remaining_feature_rows"), \
             patch.object(season_projection, "_project_stat_leaderboard", return_value=[]):
            result = season_projection._leaderboards(storage, MagicMock(), {}, _season_inputs())

        assert set(result.keys()) == set(season_projection.PLAYER_PROP_STATS)

    def test_every_stats_model_failing_returns_an_empty_dict_not_none(self):
        storage = MagicMock()
        storage.get_all_player_game_stats.return_value = []

        with patch.object(season_projection, "_depth_chart_feature_rows", return_value=({}, _empty_candidates())), \
             patch.object(season_projection, "_fill_remaining_feature_rows"), \
             patch.object(season_projection, "_project_stat_leaderboard", side_effect=Exception("boom")):
            result = season_projection._leaderboards(storage, MagicMock(), {}, _season_inputs())

        assert result == {}
