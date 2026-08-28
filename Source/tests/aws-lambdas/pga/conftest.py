import importlib.util
import os
import sys

import pytest

# RAW_BUCKET_NAME is read at module level by ingest/schedule-sync's own
# handlers -- set it before loading either module so the import doesn't
# raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so ingest,
    normalize, and schedule-sync can all coexist in the same pytest
    session without the generic 'handler' name colliding in sys.modules.
    Import failures are swallowed rather than raised."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


_load_handler("pga_ingest", "aws-lambdas/pga/ingest/handler.py")
_load_handler("pga_normalize", "aws-lambdas/pga/normalize/handler.py")
_load_handler("pga_schedule_sync", "aws-lambdas/pga/schedule-sync/handler.py")

# predict/'s own modules (live_features.py, model_loader.py,
# event_prediction.py) have unique names -- a plain sys.path entry is
# enough for them, no _load_handler renaming trick needed. predict/
# handler.py itself still needs one, same reasoning as ingest/normalize
# above -- same pattern tests/aws-lambdas/nba/conftest.py already uses.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "pga", "predict"))
_load_handler("pga_predict", "aws-lambdas/pga/predict/handler.py")

# predict-read/'s handler.py only ever imports from library.* (no local
# sibling modules the way predict/'s model_loader.py etc. are) -- no
# sys.path insert needed, just the same unique-module-name registration.
_load_handler("pga_predict_read", "aws-lambdas/pga/predict-read/handler.py")

# live-scores/'s own live_scores.py has a unique name -- same split as
# predict/'s modules above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "pga", "live-scores"))
_load_handler("pga_live_scores", "aws-lambdas/pga/live-scores/handler.py")


@pytest.fixture(autouse=True)
def reset_pga_predict_singletons():
    """Clears pga_predict's own module-level singletons before and after
    every test in this directory."""
    pga_predict = sys.modules.get("pga_predict")
    if pga_predict is None:
        yield
        return
    pga_predict._storage = None
    pga_predict._model_bucket = None
    pga_predict._predictions_table = None
    yield
    pga_predict._storage = None
    pga_predict._model_bucket = None
    pga_predict._predictions_table = None


@pytest.fixture(autouse=True)
def reset_pga_predict_read_singletons():
    pga_predict_read = sys.modules.get("pga_predict_read")
    if pga_predict_read is None:
        yield
        return
    pga_predict_read._storage = None
    pga_predict_read._model_bucket = None
    pga_predict_read._predictions_table = None
    pga_predict_read._predict_invoker = None
    yield
    pga_predict_read._storage = None
    pga_predict_read._model_bucket = None
    pga_predict_read._predictions_table = None
    pga_predict_read._predict_invoker = None
