"""
prediction-scheduler handler wiring only -- the decision logic itself
(refresh/snapshot windows, claims, retries) is covered by
tests/library/serving/test_prediction_scheduler.py.
"""
import json
import sys
from unittest.mock import MagicMock, patch

shared_prediction_scheduler = sys.modules["shared_prediction_scheduler"]


def test_handler_runs_a_tick_and_returns_its_summary(monkeypatch):
    monkeypatch.setenv("PROJECT_NAME", "proj")
    seen = {}

    def fake_run_tick(now, sports, list_events, predictions_table, invoke, project):
        seen.update(sports=sports, project=project, predictions_table=predictions_table)
        invoke("proj-nfl-predict", {"detail-type": "SnapshotPrediction", "event_id": "1"})
        return {"nfl": {"refreshes": 0, "snapshots": 1}}

    lambda_client = MagicMock()
    with patch.object(shared_prediction_scheduler.prediction_scheduler, "run_tick", fake_run_tick), \
         patch.object(shared_prediction_scheduler, "_get_lambda_client", return_value=lambda_client), \
         patch.object(shared_prediction_scheduler, "_get_events_table", return_value="events"), \
         patch.object(shared_prediction_scheduler, "_get_predictions_table", return_value="predictions"):
        result = shared_prediction_scheduler.lambda_handler({}, None)

    assert result == {"nfl": {"refreshes": 0, "snapshots": 1}}
    assert seen["project"] == "proj"
    assert seen["predictions_table"] == "predictions"
    assert set(seen["sports"]) == {"nfl", "ncaafb", "nba", "ncaambb", "pga", "f1"}
    call = lambda_client.invoke.call_args.kwargs
    assert call["FunctionName"] == "proj-nfl-predict"
    assert call["InvocationType"] == "Event"
    assert json.loads(call["Payload"]) == {"detail-type": "SnapshotPrediction", "event_id": "1"}
