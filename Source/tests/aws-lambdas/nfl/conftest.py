import importlib.util
import os
import sys

import pytest

# RAW_BUCKET_NAME is read at module level by the ingest handler -- set it
# before loading either module so the import doesn't raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so ingest,
    normalize, and predict can all coexist in the same pytest session
    without the generic 'handler' name colliding in sys.modules.

    A failed import here is swallowed rather than raised: each CI job in
    this directory only installs its own component's requirements, so a
    module whose dependencies aren't installed in this job just never
    gets registered -- harmless for a test file that never imports it."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nfl", "ingest"))
_load_handler("nfl_ingest", "aws-lambdas/nfl/ingest/handler.py")
_load_handler("nfl_normalize", "aws-lambdas/nfl/normalize/handler.py")
_load_handler("nfl_schedule_sync", "aws-lambdas/nfl/schedule-sync/handler.py")

sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nfl", "predict"))
_load_handler("nfl_predict", "aws-lambdas/nfl/predict/handler.py")

_load_handler("nfl_predict_read", "aws-lambdas/nfl/predict-read/handler.py")

sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nfl", "live-scores"))
_load_handler("nfl_live_scores", "aws-lambdas/nfl/live-scores/handler.py")


@pytest.fixture(autouse=True)
def reset_nfl_predict_singletons():
    """Clears nfl_predict's own module-level singletons before and after
    every test in this directory. Harmless for every other test file
    here: nfl_predict is always registered by _load_handler above
    regardless of which handler a given file actually exercises, and
    resetting three attributes that already default to None is a no-op
    for anything that never touches them. Named with the nfl_predict_
    prefix (not a bare reset_singletons) so it can't collide with
    nfl_predict_read's own separate reset_singletons fixture in
    test_predict_read.py."""
    nfl_predict = sys.modules.get("nfl_predict")
    if nfl_predict is None:
        yield
        return
    nfl_predict._storage = None
    nfl_predict._model_bucket = None
    nfl_predict._predictions_table = None
    yield
    nfl_predict._storage = None
    nfl_predict._model_bucket = None
    nfl_predict._predictions_table = None