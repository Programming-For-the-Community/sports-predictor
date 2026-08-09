"""
Unit tests for the NFL inference Lambda's RUSHING-specific leader
behavior -- the top-2-by-predicted-yards cap event_prediction.py enforces
after scoring the full rushing candidate pool (RB + a scrambling QB, see
live_features._LEADER_POSITIONS). Split out of what used to be one large
test_predict.py -- see test_predict_event_outcome.py's own history note,
and test_predict_receiving_props.py/test_predict_leaders.py for this same
mechanism's other category-specific and category-agnostic tests.

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


class TestRushingLeaders:
    def test_rushing_leaders_are_capped_at_top_2(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {
                "passing": [], "receiving": [], "sacks": [],
                "rushing": [_candidate_row(f"rb{i}") for i in range(1, 5)],
            },
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }
        predicted_yards = {"rb1": 30.0, "rb2": 110.0, "rb3": 85.0, "rb4": 10.0}

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", side_effect=lambda b, c, row: predicted_yards.get(row.get("entity_id"), 0.0)):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        rushing = json.loads(response["body"])["leaders"]["home"]["rushing"]
        assert [r["entity_id"] for r in rushing] == ["rb2", "rb3"]
