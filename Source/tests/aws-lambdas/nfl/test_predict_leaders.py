"""
Unit tests for the NFL inference Lambda's `leaders` block -- the shared
scoring/recording/error-resilience mechanism event_prediction.
predict_event_leaders uses for every category (passing/receiving/rushing/
sacks) alike, exercised here via passing candidates since only one is
ever scored (no ranking/capping behavior to worry about, unlike the other
three categories -- see test_predict_receiving_props.py/
test_predict_rushing_props.py for THAT category-specific behavior). Split
out of what used to be one large test_predict.py -- see
test_predict_event_outcome.py's own history note.

The nfl_predict module is registered in sys.modules by conftest.py, whose
reset_nfl_predict_singletons fixture (autouse) resets nfl_predict._storage/
_model_bucket/_predictions_table before and after every test here.
"""
import json
from unittest.mock import MagicMock, patch

import live_features
import model_loader
import nfl_predict


def _api_event(resource: str, path_params: dict | None = None, query_params: dict | None = None) -> dict:
    return {
        "resource": resource,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
    }


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


def _candidate_row(entity_id: str) -> dict:
    return {"entity_id": entity_id, "home_elo": 1500}


class TestEventOutcomeRouteLeaders:
    def test_leaders_block_includes_player_name_when_the_entity_has_one(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=267.0):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        body = json.loads(response["body"])
        passing = body["leaders"]["home"]["passing"]
        assert passing["entity_id"] == "qb1"
        assert passing["name"] == "Patrick Mahomes"
        assert passing["passing_yards"] == 267.0

    def test_leader_predictions_are_recorded_same_as_a_manual_player_prop_query(self):
        # Same model_key shape _predict_player_prop already writes -- what
        # lets nfl_reads._leaders_comparison read either kind back later
        # for a completed event, see test_predict_event_outcome.py's own
        # audits-one-write-per-model test for the core-prediction
        # counterpart of this.
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(3))), \
             patch.object(model_loader, "predict", return_value=267.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        # 4 core predictions + 2 leader stats (passing_yards, passing_touchdowns
        # -- LEADER_CATEGORY_STATS["passing"]) for the one passing candidate.
        assert nfl_predict._predictions_table.put_item.call_count == 6
        leader_calls = [
            c for c in nfl_predict._predictions_table.put_item.call_args_list
            if "PLAYER#qb1" in c.args[0]["model_key"]
        ]
        assert len(leader_calls) == 2
        assert leader_calls[0].args[0]["model_key"] == "MODEL#player-prop-passing-yards#v3#PLAYER#qb1"
        assert leader_calls[0].args[0]["predicted_value"] == {"value": 267.0}

    def test_leader_prediction_recording_failure_does_not_break_the_leaders_block(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = {"entity_id": "qb1", "name": "Patrick Mahomes"}
        # First 4 put_item calls (core predictions) succeed; the leader
        # writes are what should fail here without taking anything down.
        nfl_predict._predictions_table.put_item.side_effect = [None, None, None, None, Exception("DynamoDB down")]

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(3))), \
             patch.object(model_loader, "predict", return_value=267.0):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        passing = json.loads(response["body"])["leaders"]["home"]["passing"]
        assert passing["passing_yards"] == 267.0

    def test_leaders_is_none_when_candidate_building_fails_but_core_predictions_still_succeed(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", side_effect=RuntimeError("boom")), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=0.5):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["leaders"] is None
        assert "win_probability" in body["predictions"]

    def test_a_missing_prop_model_for_one_stat_is_skipped_not_fatal(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {"passing": [_candidate_row("qb1")], "receiving": [], "rushing": [], "sacks": []},
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", side_effect=[
                 (MagicMock(), _model_card(1)),  # win-probability
                 (MagicMock(), _model_card(2)),  # margin
                 (MagicMock(), _model_card(3)),  # home-score
                 (MagicMock(), _model_card(4)),  # away-score
                 model_loader.NoPromotedModelError("no passing_yards model yet"),
                 (MagicMock(), _model_card(5)),  # passing_touchdowns
             ]), \
             patch.object(model_loader, "predict", side_effect=[0.62, 3.2, 24.1, 20.9, 2.0]):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        assert response["statusCode"] == 200
        passing = json.loads(response["body"])["leaders"]["home"]["passing"]
        assert "passing_yards" not in passing
        assert passing["passing_touchdowns"] == 2.0

    def test_reuses_a_loaded_model_across_candidates_sharing_the_same_stat(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {
                "passing": [], "rushing": [], "sacks": [],
                "receiving": [_candidate_row("wr1"), _candidate_row("wr2")],
            },
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }
        load_call_count = {"n": 0}

        def fake_load(s3, sport, model_name):
            load_call_count["n"] += 1
            return (MagicMock(), _model_card(1))

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", side_effect=fake_load), \
             patch.object(model_loader, "predict", return_value=50.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        # 4 core models (win-probability, margin, home-score, away-score)
        # + 2 DISTINCT receiving models (yards, touchdowns) -- NOT 4,
        # even though there are 2 receiver candidates each needing both.
        assert load_call_count["n"] == 6
