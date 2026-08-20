"""
Unit tests for the NFL inference Lambda's scheduled season-projection
path: season_projection._season_standings_inputs (wins/losses/ties/point
differential/Elo carryover/remaining-games derivation from stored events)
and the EventBridge-triggered ScheduledSeasonProjection handler branch
that runs season_simulation.simulate_season and writes the result to S3.

The nfl_predict module is registered in sys.modules by conftest.py, whose
reset_nfl_predict_singletons fixture (autouse) resets nfl_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
from unittest.mock import MagicMock, patch

import pytest

import live_features
import model_loader
import nfl_predict
import season_projection
import season_simulation


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


def _completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                      event_id=None, event_date="2025-09-14", season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "completed",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


def _scheduled_event(event_key, season, event_date, home_id, away_id, *,
                      event_id=None, season_type=2, week=1):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "scheduled",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }


class TestSeasonStandingsInputs:
    def test_derives_wins_losses_and_point_differential_from_completed_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["wins"]["12"] == 1
        assert inputs["losses"]["24"] == 1
        assert inputs["point_differential"]["12"] == 7
        assert inputs["point_differential"]["24"] == -7

    def test_a_tie_counts_toward_neither_teams_wins_or_losses(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 20, 20)],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["ties"]["12"] == 1
        assert inputs["ties"]["24"] == 1
        assert inputs["wins"].get("12", 0) == 0
        assert inputs["losses"].get("12", 0) == 0

    def test_current_season_is_the_max_season_across_both_statuses(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2024, "12", "24", 27, 20)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["current_season"] == 2025
        # The 2024 completed game shouldn't leak into this season's record.
        assert inputs["wins"] == {}

    def test_elo_carries_over_regressed_into_a_new_season_not_a_hard_reset(self):
        # A blowout in the prior season swings team "12"'s Elo rating well
        # above DEFAULT_STARTING_RATING -- confirm the new season's
        # current_ratings reflects a partial carryover (regressed toward
        # the default per compute_elo_ratings' season_carryover), not a
        # full reset and not the full unregressed rating either.
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2024, "12", "24", 45, 3)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert 1500 < inputs["current_ratings"]["12"] < 1531
        assert 1469 < inputs["current_ratings"]["24"] < 1500

    def test_remaining_games_and_team_next_event_reflect_chronological_order(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [
                _scheduled_event("E2", 2025, "2025-09-28", "12", "25"),
                _scheduled_event("E1", 2025, "2025-09-21", "12", "7"),
            ],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["remaining_games"] == [("12", "7"), ("12", "25")]
        assert inputs["team_next_event"]["12"] == "E1"  # earliest game, not insertion order
        assert inputs["games_remaining"]["12"] == 2


class TestScheduledSeasonProjection:
    """GET /nfl/season is served from predict-read/handler.py. This Lambda
    computes the projection on the weekly direct EventBridge Scheduler
    invoke and writes it to S3."""

    def test_writes_the_season_projection_to_s3_under_the_expected_key(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._predictions_table.query.return_value = []
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.return_value = []

        simulated = {
            "12": {"projected_wins": 11.0, "division_winner_probability": 0.8, "playoff_probability": 0.9, "championship_probability": 0.2},
            "24": {"projected_wins": 6.0, "division_winner_probability": 0.1, "playoff_probability": 0.3, "championship_probability": 0.01},
        }
        with patch.object(season_simulation, "simulate_season", return_value=simulated):
            response = nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        nfl_predict._model_bucket.put_json.assert_called_once()
        key, body = nfl_predict._model_bucket.put_json.call_args[0]
        assert key == "season-projections/nfl/latest.json"
        assert body["season"] == 2025
        assert [row["team_id"] for row in body["standings"]] == ["12", "24"]
        assert body["standings"][0]["wins"] == 1
        assert body["standings"][0]["ties"] == 0

    def test_leaderboards_is_none_when_building_them_fails_but_standings_still_write(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._predictions_table.query.return_value = []
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.side_effect = RuntimeError("boom")

        with patch.object(season_simulation, "simulate_season", return_value={}):
            nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nfl_predict._model_bucket.put_json.call_args[0][1]
        assert body["leaderboards"] is None
        assert body["standings"] == []

    def test_leaderboards_include_player_names_and_are_capped_at_ten(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._predictions_table.query.return_value = []
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.return_value = [
            {"entity_id": "qb1", "team_id": "12", "event_key": "E1", "stat_line": {"passing_yards": 300}},
        ]
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(live_features, "build_live_player_features", return_value={"entity_id": "qb1"}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=280.0):
            nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nfl_predict._model_bucket.put_json.call_args[0][1]
        passing_leaders = body["leaderboards"]["passing_yards"]
        assert len(passing_leaders) <= 10
        assert passing_leaders[0]["name"] == "Patrick Mahomes"
        # current 300 + one remaining game projected at 280/game
        assert passing_leaders[0]["projected_total"] == pytest.approx(580.0)

    def test_leaderboards_include_season_wide_candidates_with_zero_recorded_stats(self):
        # The pre-season case: no completed games yet this season, so
        # current_totals_by_stat is empty for every stat -- candidates
        # must come entirely from each team's own next-event depth chart
        # (build_live_event_leader_candidates), not season_player_stats.
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._predictions_table.query.return_value = []
        nfl_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [_scheduled_event("E2", 2026, "2026-09-10", "12", "7")],
        }[status]
        nfl_predict._storage.get_all_player_game_stats.return_value = []
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        candidates = {
            "home": {"passing": [{"entity_id": "qb1", "team_id": "12"}], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=265.0):
            nfl_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nfl_predict._model_bucket.put_json.call_args[0][1]
        passing_leaders = body["leaderboards"]["passing_yards"]
        assert len(passing_leaders) == 1
        assert passing_leaders[0]["entity_id"] == "qb1"
        assert passing_leaders[0]["current_total"] == 0.0
        # No recorded games -- projected total is purely the per-game
        # projection times this team's one remaining game.
        assert passing_leaders[0]["projected_total"] == pytest.approx(265.0)
