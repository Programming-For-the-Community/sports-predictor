"""
Unit tests for RUSHING-specific leader behavior -- the top-2-by-
predicted-yards cap event_prediction.py enforces after scoring the full
rushing candidate pool (RB + a scrambling QB, see live_features.
_LEADER_POSITIONS). Split out of what used to be one large
test_predict.py -- see test_predict_event_outcome.py's own history note,
and test_predict_receiving_props.py/test_predict_leaders.py for this
same mechanism's other category-specific and category-agnostic tests.

Calls event_prediction.predict_event directly, not through nfl_predict.
lambda_handler -- see test_predict_event_outcome.py's own docstring for
why.
"""
from unittest.mock import MagicMock, patch

import event_prediction
import live_features
import model_loader


def _model_card(version: int) -> dict:
    return {"version": version, "feature_columns": []}


def _candidate_row(entity_id: str) -> dict:
    return {"entity_id": entity_id, "home_elo": 1500}


class TestRushingLeaders:
    def test_rushing_leaders_are_capped_at_top_2(self):
        storage, s3, predictions_table = MagicMock(), MagicMock(), MagicMock()
        storage.get_entity.return_value = None

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
            result = event_prediction.predict_event(storage, s3, predictions_table, "401547417")

        rushing = result["leaders"]["home"]["rushing"]
        assert [r["entity_id"] for r in rushing] == ["rb2", "rb3"]
