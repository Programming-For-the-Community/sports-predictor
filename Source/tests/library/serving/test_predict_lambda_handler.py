"""
Unit tests for library.serving.predict_lambda_handler.
make_event_prediction_lambda_handler -- a predict Lambda wired to a
sport's own event_prediction module through its ServingResources. Each
sport's test_predict_handler.py covers it through that sport's handler.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from library.serving.predict_lambda_handler import make_event_prediction_lambda_handler

_CORE = ("storage", "bucket", "table")


def _resources():
    return SimpleNamespace(
        storage=MagicMock(return_value="storage"),
        model_bucket=MagicMock(return_value="bucket"),
        predictions_table=MagicMock(return_value="table"),
        all=MagicMock(return_value=_CORE),
    )


def _event_prediction():
    return SimpleNamespace(
        compute_and_cache_event=MagicMock(), compute_and_cache_player_prop=MagicMock(), snapshot_event=MagicMock(return_value=1),
    )


def _handler(resources, event_prediction, **kwargs):
    kwargs.setdefault("player_props", True)
    return make_event_prediction_lambda_handler(
        resources=resources, event_prediction=event_prediction, run_scheduled_fn=MagicMock(return_value={"ok": 1}),
        logger=logging.getLogger("test-predict"), **kwargs,
    )


def test_warmup_creates_every_singleton_including_extras():
    resources, extra = _resources(), MagicMock()

    assert _handler(resources, _event_prediction(), extra_warmups=(extra,))({"warmup": True}, None) == {"status": "warm"}

    for getter in (resources.storage, resources.model_bucket, resources.predictions_table, extra):
        getter.assert_called_once_with()


def test_event_player_prop_and_snapshot_routes_lead_with_the_shared_resources():
    event_prediction = _event_prediction()
    handler = _handler(_resources(), event_prediction)

    handler({"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": "e1"}, None)
    handler({"detail-type": "ComputeAndCachePrediction", "route": "player_prop", "event_id": "e1", "entity_id": "p1", "stat": "points"}, None)
    snapshot = handler({"detail-type": "SnapshotPrediction", "event_id": "e1"}, None)

    event_prediction.compute_and_cache_event.assert_called_once_with(*_CORE, "e1")
    event_prediction.compute_and_cache_player_prop.assert_called_once_with(*_CORE, "e1", "p1", "points")
    assert snapshot == {"status": "ok", "snapshotted": 1}


def test_without_player_props_that_route_is_unrecognized():
    event_prediction = _event_prediction()

    response = _handler(_resources(), event_prediction, player_props=False)(
        {"detail-type": "ComputeAndCachePrediction", "route": "player_prop", "event_id": "e1", "entity_id": "p1", "stat": "x"}, None,
    )

    assert response["status"] == "error"
    event_prediction.compute_and_cache_player_prop.assert_not_called()
