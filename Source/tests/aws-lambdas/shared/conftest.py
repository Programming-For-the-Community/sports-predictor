import importlib.util
import os
import sys

import pytest

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Same unique-module-name registration trick as tests/aws-lambdas/
    nfl/conftest.py, including the same ImportError swallow: each CI job
    in this directory only installs its own component's requirements
    (e.g. test_season_gate doesn't install cloudwatch-geo-widget's
    Pillow), and this conftest loads every handler here regardless of
    which test file is actually running -- a module whose dependencies
    aren't installed in this job just never gets registered, harmless
    for a test file that never imports it."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


_load_handler("shared_season_gate", "aws-lambdas/shared/season-gate/handler.py")
_load_handler("shared_cloudwatch_geo_widget", "aws-lambdas/shared/cloudwatch-geo-widget/handler.py")
_load_handler("shared_ec2_training_reaper", "aws-lambdas/shared/ec2-training-reaper/handler.py")
_load_handler("shared_predict_read", "aws-lambdas/shared/predict-read/handler.py")


@pytest.fixture(autouse=True)
def reset_shared_predict_read_singletons():
    """Clears shared_predict_read's own module-level singletons before and
    after every test in this directory -- same pattern every per-sport
    conftest.py already used for its own (now-removed) predict-read
    module. _predict_invokers is a dict (one LambdaInvoker per sport, not
    a single value) since this Lambda now serves all 6 sports."""
    shared_predict_read = sys.modules.get("shared_predict_read")
    if shared_predict_read is None:
        yield
        return
    shared_predict_read._storage = None
    shared_predict_read._model_bucket = None
    shared_predict_read._predictions_table = None
    shared_predict_read._predict_invokers = {}
    yield
    shared_predict_read._storage = None
    shared_predict_read._model_bucket = None
    shared_predict_read._predictions_table = None
    shared_predict_read._predict_invokers = {}
