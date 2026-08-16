"""
Unit tests for the NBA inference Lambda's scheduled season-projection
path: season_projection._season_standings_inputs (wins/losses/point
differential/Elo carryover/remaining-games/Cup-game derivation from
stored events) and the EventBridge-triggered ScheduledSeasonProjection
handler branch that runs season_simulation.simulate_season/simulate_cup
and writes the result to S3. Mirrors NFL's own
test_predict_season_simulation.py, minus ties (the NBA has none) and with
NBA's own 6-stat player-prop set and NBA Cup block.

The nba_predict module is registered in sys.modules by conftest.py, whose
reset_nba_predict_singletons fixture (autouse) resets nba_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
from unittest.mock import MagicMock, patch

import pytest

import live_features
import model_loader
import nba_predict
import season_projection
import season_simulation


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


def _completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                      event_id=None, event_date="2025-11-14", tournament_note=None):
    item = {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "status": "completed",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }
    if tournament_note:
        item["tournament_note"] = tournament_note
    return item


def _scheduled_event(event_key, season, event_date, home_id, away_id, *, event_id=None, tournament_note=None):
    item = {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "status": "scheduled",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }
    if tournament_note:
        item["tournament_note"] = tournament_note
    return item


class TestSeasonStandingsInputs:
    def test_derives_wins_losses_and_point_differential_from_completed_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2026, "13", "12", 112, 100)],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["wins"]["13"] == 1
        assert inputs["losses"]["12"] == 1
        assert inputs["point_differential"]["13"] == 12
        assert inputs["point_differential"]["12"] == -12

    def test_current_season_is_the_max_season_across_both_statuses(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "13", "12", 112, 100)],
            "scheduled": [_scheduled_event("E2", 2026, "2025-11-21", "13", "2")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["current_season"] == 2026
        # The 2025 completed game shouldn't leak into this season's record.
        assert inputs["wins"] == {}

    def test_elo_carries_over_regressed_into_a_new_season_not_a_hard_reset(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "13", "12", 130, 90)],
            "scheduled": [_scheduled_event("E2", 2026, "2025-11-21", "13", "2")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert 1500 < inputs["current_ratings"]["13"] < 1531
        assert 1469 < inputs["current_ratings"]["12"] < 1500

    def test_remaining_games_and_team_next_event_reflect_chronological_order(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [
                _scheduled_event("E2", 2026, "2025-11-28", "13", "25"),
                _scheduled_event("E1", 2026, "2025-11-21", "13", "2"),
            ],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["remaining_games"] == [("13", "2"), ("13", "25")]
        assert inputs["team_next_event"]["13"] == "E1"  # earliest game, not insertion order
        assert inputs["games_remaining"]["13"] == 2

    def test_cup_group_play_games_are_tracked_separately_from_the_full_season_record(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [
                _completed_event("E1", 2026, "13", "12", 112, 100, tournament_note="NBA Cup - Group Play"),
                _completed_event("E2", 2026, "13", "2", 99, 95),  # regular game, not Cup
            ],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["wins"]["13"] == 2  # both games count toward the real record
        assert inputs["cup_wins"]["13"] == 1  # only the Cup game counts toward the Cup record
        assert inputs["cup_losses"].get("13", 0) == 0

    def test_remaining_cup_games_are_a_subset_of_remaining_games(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [
                _scheduled_event("E1", 2026, "2025-11-14", "13", "2", tournament_note="NBA Cup - Group Play"),
                _scheduled_event("E2", 2026, "2025-12-14", "13", "12"),  # not a Cup game
            ],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert len(inputs["remaining_games"]) == 2
        assert inputs["remaining_cup_games"] == [("13", "2")]


class TestScheduledSeasonProjection:
    """GET /nba/season doesn't terminate on this Lambda -- moved to
    predict-read/handler.py (see library.serving.nba_reads.
    get_season_projection). This Lambda instead computes the projection
    on Terraform/scheduler-nba-season-projection.tf's weekly direct
    EventBridge Scheduler invoke and writes it to S3."""

    def test_writes_the_season_projection_to_s3_under_the_expected_key(self):
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2026, "13", "12", 112, 100)],
            "scheduled": [],
        }[status]
        nba_predict._storage.get_all_player_game_stats.return_value = []

        simulated = {
            "13": {"projected_wins": 55.0, "division_winner_probability": 0.8, "play_in_probability": 0.1,
                   "playoff_probability": 0.9, "championship_probability": 0.2},
            "12": {"projected_wins": 30.0, "division_winner_probability": 0.05, "play_in_probability": 0.3,
                   "playoff_probability": 0.2, "championship_probability": 0.01},
        }
        with patch.object(season_simulation, "simulate_season", return_value=simulated), \
             patch.object(season_simulation, "simulate_cup", return_value=None):
            response = nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        nba_predict._model_bucket.put_json.assert_called_once()
        key, body = nba_predict._model_bucket.put_json.call_args[0]
        assert key == "season-projections/nba/latest.json"
        assert body["season"] == 2026
        assert [row["team_id"] for row in body["standings"]] == ["13", "12"]
        assert body["standings"][0]["wins"] == 1
        assert body["cup"] is None

    def test_cup_is_none_when_the_seasons_groups_are_not_in_the_table(self):
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2099, "13", "12", 112, 100)],
            "scheduled": [],
        }[status]
        nba_predict._storage.get_all_player_game_stats.return_value = []

        with patch.object(season_simulation, "simulate_season", return_value={}):
            nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nba_predict._model_bucket.put_json.call_args[0][1]
        assert body["cup"] is None

    def test_cup_groups_are_populated_when_the_season_is_in_the_table(self):
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2026, "13", "12", 112, 100)],
            "scheduled": [],
        }[status]
        nba_predict._storage.get_all_player_game_stats.return_value = []
        nba_predict._storage.get_entity.return_value = None

        with patch.object(season_simulation, "simulate_season", return_value={}):
            nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nba_predict._model_bucket.put_json.call_args[0][1]
        assert "Eastern B" in body["cup"]["groups"]  # BOS's real group -- see CUP_GROUPS[2026]
        assert len(body["cup"]["groups"]["Eastern B"]) == 5

    def test_cup_is_none_when_building_it_raises_but_the_write_still_happens(self):
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2026, "13", "12", 112, 100)],
            "scheduled": [],
        }[status]
        nba_predict._storage.get_all_player_game_stats.return_value = []

        simulated = {"13": {"projected_wins": 1.0}}
        with patch.object(season_simulation, "simulate_season", return_value=simulated), \
             patch.object(season_simulation, "simulate_cup", side_effect=RuntimeError("boom")):
            response = nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = nba_predict._model_bucket.put_json.call_args[0][1]
        assert body["cup"] is None
        assert body["standings"] != []  # the cup failure didn't take standings down with it

    def test_leaderboards_is_none_when_building_them_fails_but_standings_still_write(self):
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2026, "13", "12", 112, 100)],
            "scheduled": [],
        }[status]
        nba_predict._storage.get_all_player_game_stats.side_effect = RuntimeError("boom")

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(season_simulation, "simulate_cup", return_value=None):
            nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nba_predict._model_bucket.put_json.call_args[0][1]
        assert body["leaderboards"] is None
        assert body["standings"] == []

    def test_leaderboards_include_player_names_and_are_capped_at_ten(self):
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2026, "13", "12", 112, 100)],
            "scheduled": [_scheduled_event("E2", 2026, "2025-11-21", "13", "2")],
        }[status]
        nba_predict._storage.get_all_player_game_stats.return_value = [
            {"entity_id": "p1", "team_id": "13", "event_key": "E1", "stat_line": {"points": 27}},
        ]
        nba_predict._storage.get_entity.return_value = {"entity_id": "p1", "name": "Jayson Tatum"}

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(season_simulation, "simulate_cup", return_value=None), \
             patch.object(live_features, "build_live_player_features", return_value={"entity_id": "p1"}), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=25.0):
            nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nba_predict._model_bucket.put_json.call_args[0][1]
        scoring_leaders = body["leaderboards"]["points"]
        assert len(scoring_leaders) <= 10
        assert scoring_leaders[0]["name"] == "Jayson Tatum"
        # current 27 + one remaining game projected at 25/game
        assert scoring_leaders[0]["projected_total"] == pytest.approx(52.0)

    def test_leaderboards_include_season_wide_candidates_with_zero_recorded_stats(self):
        # The pre-season case: no completed games yet this season, so
        # current_totals_by_stat is empty for every stat -- candidates
        # must come entirely from each team's own next-event recent-
        # volume search (build_live_event_leader_candidates), not
        # season_player_stats.
        nba_predict._storage = MagicMock()
        nba_predict._model_bucket = MagicMock()
        nba_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [_scheduled_event("E2", 2026, "2025-10-22", "13", "2")],
        }[status]
        nba_predict._storage.get_all_player_game_stats.return_value = []
        nba_predict._storage.get_entity.return_value = {"entity_id": "p1", "name": "Jayson Tatum"}

        candidates = {
            "home": {"scoring": [{"entity_id": "p1", "team_id": "13"}], "rebounding": [], "assists": []},
            "away": {"scoring": [], "rebounding": [], "assists": []},
        }

        with patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(season_simulation, "simulate_cup", return_value=None), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=22.0):
            nba_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        body = nba_predict._model_bucket.put_json.call_args[0][1]
        scoring_leaders = body["leaderboards"]["points"]
        assert len(scoring_leaders) == 1
        assert scoring_leaders[0]["entity_id"] == "p1"
        assert scoring_leaders[0]["current_total"] == 0.0
        assert scoring_leaders[0]["projected_total"] == pytest.approx(22.0)
