import logging

from library.serving import predict_lambda_handler


class TestDispatch:
    def _handler(self, snapshot_event_fn):
        return predict_lambda_handler.make_lambda_handler(
            warmup_fn=lambda: None, run_scheduled_fn=lambda: None, compute_and_cache_event_fn=lambda event_id: None,
            compute_and_cache_player_prop_fn=lambda *a: None, snapshot_event_fn=snapshot_event_fn,
            logger=logging.getLogger("test"),
        )

    def test_snapshot_prediction_routes_to_snapshot_fn(self):
        calls = []
        handler = self._handler(lambda event_id: calls.append(event_id) or 4)

        result = handler({"detail-type": "SnapshotPrediction", "event_id": "401"}, None)

        assert calls == ["401"]
        assert result == {"status": "ok", "snapshotted": 4}

    def test_a_sport_without_snapshot_support_treats_it_as_unrecognized(self):
        handler = self._handler(None)

        assert handler({"detail-type": "SnapshotPrediction", "event_id": "401"}, None)["status"] == "error"
