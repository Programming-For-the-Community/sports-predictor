"""
Unit tests for the NFL inference Lambda's RECEIVING-specific leader
behavior: live_features._team_leader_candidates deliberately leaves this
one category's candidate pool uncapped (every eligible WR/TE/RB/QB across
the depth chart, see that function's own docstring), which makes
event_prediction.py itself responsible for ranking by predicted yards and
cutting down to the top 3 -- and for only writing THOSE top 3 to the
predictions-table audit trail, not the whole pool it scored along the
way. Split out of what used to be one large test_predict.py -- see
test_predict_event_outcome.py's own history note, and
test_predict_rushing_props.py/test_predict_leaders.py for this same
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


class TestReceivingLeaders:
    def test_receiving_leaders_are_capped_at_top_3_by_predicted_yards(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {
                "passing": [], "rushing": [], "sacks": [],
                "receiving": [_candidate_row(f"wr{i}") for i in range(1, 6)],
            },
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }
        predicted_yards = {"wr1": 40.0, "wr2": 90.0, "wr3": 60.0, "wr4": 20.0, "wr5": 75.0}

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", side_effect=lambda b, c, row: predicted_yards.get(row.get("entity_id"), 0.0)):
            response = nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        receiving = json.loads(response["body"])["leaders"]["home"]["receiving"]
        assert [r["entity_id"] for r in receiving] == ["wr2", "wr5", "wr3"]

    def test_only_the_capped_receiving_leaders_are_recorded_to_the_audit_trail(self):
        nfl_predict._storage = MagicMock()
        nfl_predict._model_bucket = MagicMock()
        nfl_predict._predictions_table = MagicMock()
        nfl_predict._storage.get_entity.return_value = None

        candidates = {
            "home": {
                "passing": [], "rushing": [], "sacks": [],
                "receiving": [_candidate_row(f"wr{i}") for i in range(1, 6)],
            },
            "away": {"passing": [], "receiving": [], "rushing": [], "sacks": []},
        }

        with patch.object(live_features, "build_live_event_features", return_value={"home_elo": 1500}), \
             patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates), \
             patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(model_loader, "predict", return_value=50.0):
            nfl_predict.lambda_handler(
                _api_event("/nfl/predictions/events/{event_id}", {"event_id": "401547417"}), None,
            )

        # 4 core + 2 stats * 3 capped receiving winners = 10, NOT 4 + 2*5 --
        # all 5 candidates get SCORED, but only the top 3 get WRITTEN to
        # the audit trail nfl_reads._leaders_comparison later reads back.
        assert nfl_predict._predictions_table.put_item.call_count == 10
