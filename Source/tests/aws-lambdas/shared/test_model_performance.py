"""
model-performance handler wiring only -- the scoring itself is covered by
tests/library/performance/.
"""
import sys
from unittest.mock import patch

shared_model_performance = sys.modules["shared_model_performance"]


def _patched(run_sport):
    return patch.multiple(
        shared_model_performance,
        _get_storage=lambda: "storage", _get_predictions_table=lambda: "predictions", _get_model_bucket=lambda: "bucket",
    ), patch.object(shared_model_performance.runner, "run_sport", run_sport)


def test_runs_every_sport_by_default():
    ran = []

    def run_sport(sport, storage, predictions_table, s3, today):
        ran.append(sport)
        return {"models": [1, 2], "coverage": {"completed_events": 5, "with_prediction": 4, "unpredicted": 1}}

    patches, run_patch = _patched(run_sport)
    with patches, run_patch:
        summary = shared_model_performance.lambda_handler({}, None)

    assert ran == ["nfl", "ncaafb", "nba", "ncaambb", "pga", "f1"]
    assert summary["nfl"] == {"status": "ok", "models": 2, "completed_events": 5, "with_prediction": 4, "unpredicted": 1}


def test_a_payload_can_limit_the_sports():
    ran = []
    patches, run_patch = _patched(lambda sport, *a: ran.append(sport) or {"models": [], "coverage": {}})
    with patches, run_patch:
        shared_model_performance.lambda_handler({"sports": ["nba"]}, None)

    assert ran == ["nba"]


def test_one_sport_failing_does_not_stop_the_others():
    def run_sport(sport, *args):
        if sport == "nfl":
            raise RuntimeError("boom")
        return {"models": [], "coverage": {}}

    patches, run_patch = _patched(run_sport)
    with patches, run_patch:
        summary = shared_model_performance.lambda_handler({}, None)

    assert summary["nfl"] == {"status": "error"}
    assert summary["nba"]["status"] == "ok"
