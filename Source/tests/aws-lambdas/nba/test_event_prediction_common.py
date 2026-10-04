"""
library.serving.event_prediction_common._score_and_record_leader and
snapshot_event -- lives
here rather than tests/library/serving since the module imports its
sport's own live_features, which only resolves with a predict/ directory
on sys.path (see this folder's conftest.py).
"""
from unittest.mock import MagicMock, patch

from library.serving import event_prediction_common, model_loader, prediction_snapshots, serving_features


def _score(storage, predictions_table, stats=("points", "rebounds")):
    def _load(s3, sport, model_name):
        if model_name == "player-prop-rebounds":
            raise model_loader.NoPromotedModelError(model_name)
        return MagicMock(), {"version": 2}

    with patch.object(model_loader, "load_current_model", side_effect=_load), \
         patch.object(model_loader, "predict", return_value=21.5):
        return event_prediction_common._score_and_record_leader(
            storage, MagicMock(), predictions_table, "nba", {}, "EVENT#1", {"entity_id": "p1"}, list(stats),
        )


class TestScoreAndRecordLeader:
    def test_scores_promoted_stats_names_the_player_and_skips_unpromoted_ones(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Player One"}
        predictions_table = MagicMock()

        result = _score(storage, predictions_table)

        assert result == {"entity_id": "p1", "name": "Player One", "points": 21.5}
        item = predictions_table.put_item.call_args.args[0]
        assert item["model_key"] == "MODEL#player-prop-points#v2#PLAYER#p1"
        assert item["predicted_value"] == {"value": 21.5}

    def test_a_failed_prediction_write_still_returns_the_scored_value(self):
        storage = MagicMock()
        storage.get_entity.return_value = None
        predictions_table = MagicMock()
        predictions_table.put_item.side_effect = RuntimeError("throttled")

        result = _score(storage, predictions_table, stats=("points",))

        assert result == {"entity_id": "p1", "points": 21.5}


class TestSnapshotEvent:
    def test_writes_the_inputs_the_snapshot_compute_used(self):
        s3 = MagicMock()

        def compute(storage, s3, predictions_table, event_id, sport, predict_event_fn):
            serving_features.note({"model_name": "win-probability", "version": 4}, {}, {"home_elo": 1510.0})

        with patch.object(event_prediction_common, "compute_and_cache_event", side_effect=compute),              patch.object(prediction_snapshots, "snapshot_event_predictions", return_value=3):
            written = event_prediction_common.snapshot_event(MagicMock(), s3, MagicMock(), "77", "nba", MagicMock())

        assert written == 3
        s3.put_json.assert_called_once_with("serving-features/nba/77.json", {"win-probability#v4": {"home_elo": 1510.0}})
