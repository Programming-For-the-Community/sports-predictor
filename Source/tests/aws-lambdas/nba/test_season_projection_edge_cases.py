"""
Edge cases for season_projection: malformed/partial events are skipped
rather than crashing the weekly projection, per-player feature failures
drop only that player, and each optional payload section fails on its own.
"""
from unittest.mock import MagicMock, patch

import live_features
import season_projection
import season_simulation
from library.serving import model_loader

_NO_SIDES = {"event_key": "X", "event_id": "X", "season": 2026, "season_type": 3, "status": "completed", "participants": []}


def _game(event_key, home_id, away_id, home_score, away_score, status="completed", season_type=2):
    return {
        "event_key": event_key, "event_id": event_key, "event_date": "2026-01-01", "season": 2026,
        "season_type": season_type, "status": status,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score}},
        ],
    }


class TestCompletedGameRecords:
    def test_skips_events_missing_a_side_or_a_score(self):
        unscored = _game("E2", "a", "b", None, 100)
        wins, losses, point_differential, _, _ = season_projection._completed_game_records(
            [_NO_SIDES, unscored, _game("E1", "a", "b", 110, 100)],
        )

        assert wins == {"a": 1, "b": 0}
        assert losses == {"a": 0, "b": 1}
        assert point_differential == {"a": 10, "b": -10}


class TestRemainingGameInputs:
    def test_skips_scheduled_events_missing_a_side(self):
        remaining, _, team_next_event = season_projection._remaining_game_inputs(
            [{**_NO_SIDES, "status": "scheduled"}, _game("S1", "a", "b", None, None, status="scheduled")],
        )

        assert remaining == [("a", "b")]
        assert team_next_event == {"a": "S1", "b": "S1"}


class TestRealPostseasonLookups:
    def _storage(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: (
            [_NO_SIDES, _game("P1", "a", "b", 100, 90, season_type=3)] if status == "completed" else []
        )
        return storage

    def test_matchups_skip_events_missing_a_side(self):
        assert list(season_projection._real_postseason_matchups(self._storage(), 2026)) == [frozenset(("a", "b"))]

    def test_series_skip_events_missing_a_side(self):
        assert list(season_projection._real_postseason_series(self._storage(), 2026)) == [frozenset(("a", "b"))]


class TestFillRemainingFeatureRows:
    def test_keeps_only_players_whose_live_features_built(self):
        season_inputs = {"team_next_event": {"T1": "E-next"}, "current_ratings": {}}
        player_team = {"ok": "T1", "missing-event": "T1", "boom": "T1", "no-team-game": "T2"}

        def _build(_storage, _sport, _event_key, entity_id, **_kwargs):
            if entity_id == "missing-event":
                raise live_features.EventNotFoundError("gone")
            if entity_id == "boom":
                raise RuntimeError("bad row")
            return {"entity_id": entity_id}

        cache: dict = {}
        with patch.object(live_features, "build_live_player_features", side_effect=_build):
            season_projection._fill_remaining_feature_rows(
                MagicMock(), season_inputs, player_team, cache, set(player_team),
            )

        assert cache == {"ok": {"entity_id": "ok"}}


class TestProjectStatLeaderboard:
    def test_no_promoted_model_ranks_on_current_totals_alone(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Player One"}
        stat = season_projection.PLAYER_PROP_STATS[0]

        with patch.object(season_projection.event_prediction, "get_cached_model", side_effect=model_loader.NoPromotedModelError("none")), \
             patch.object(model_loader, "predict") as predict:
            top = season_projection._project_stat_leaderboard(
                storage, MagicMock(), {}, {"games_remaining": {"T1": 5}}, stat, {"p1"},
                {stat: {"p1": 42.0}}, {"p1": {"f": 1}}, {"p1": "T1"},
            )

        predict.assert_not_called()
        assert top[0]["entity_id"] == "p1"
        assert top[0]["name"] == "Player One"


class TestResolveMatchupComputeFailure:
    def test_a_failed_live_prediction_leaves_the_slot_unpredicted_instead_of_raising(self):
        event = _game("E1", "a", "b", None, None, status="scheduled", season_type=3)
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        with patch.object(season_projection.event_prediction, "compute_and_cache_event", side_effect=RuntimeError("boom")):
            result = season_projection._resolve_matchup(
                "a", "b", 7, 8, {frozenset(("a", "b")): event}, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        assert result["status"] == "scheduled"
        assert result["predicted_winner"] is None
        assert result["win_probability"] is None


class TestBuildSeasonProjectionSectionFailures:
    def test_cup_bracket_and_bracket_failures_each_fall_back_to_none(self):
        season_inputs = {"current_season": 2026, "wins": {}, "losses": {}, "point_differential": {}, "remaining_games": [], "current_ratings": {}}
        with patch.object(season_projection, "_season_standings_inputs", return_value=season_inputs), \
             patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(season_projection, "enrich_team_standings", side_effect=lambda storage, sport, rows: rows), \
             patch.object(season_projection, "_cup_payload", return_value={"groups": {}}), \
             patch.object(season_projection, "_cup_bracket_payload", side_effect=RuntimeError("boom")), \
             patch.object(season_projection, "_leaderboards", return_value={}), \
             patch.object(season_projection, "_bracket_payload", side_effect=RuntimeError("boom")):
            result = season_projection.build_season_projection(MagicMock(), MagicMock(), MagicMock())

        assert result["cup"] == {"groups": {}}
        assert result["cup_bracket"] is None
        assert result["leaderboards"] == {}
        assert result["bracket"] is None
