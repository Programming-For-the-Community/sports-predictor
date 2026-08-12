import importlib.util
import os
import sys

import pytest

# RAW_BUCKET_NAME is read at module level by ingest/schedule-sync's own
# handlers -- set it before loading either module so the import doesn't
# raise KeyError. Same convention as aws-lambdas/nfl's own conftest.py.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so ingest,
    normalize, and schedule-sync can all coexist in the same pytest
    session without the generic 'handler' name colliding in sys.modules.
    Same pattern as aws-lambdas/nfl/conftest.py's own _load_handler --
    see that file's docstring for why import failures are swallowed
    rather than raised."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


# ingest/'s own enrichment.py has a unique name, unlike handler.py -- a
# plain sys.path entry is enough for it, same as NFL's own ingest/
# enrichment.py.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaafb", "ingest"))
_load_handler("ncaafb_ingest", "aws-lambdas/ncaafb/ingest/handler.py")
_load_handler("ncaafb_normalize", "aws-lambdas/ncaafb/normalize/handler.py")
_load_handler("ncaafb_schedule_sync", "aws-lambdas/ncaafb/schedule-sync/handler.py")

# predict/'s own modules (live_features.py, model_loader.py,
# event_prediction.py) have unique names, unlike handler.py -- a plain
# sys.path entry is enough for them, no _load_handler renaming trick
# needed. predict/handler.py itself still needs one, same reasoning as
# ingest/normalize above -- see NFL's own aws-lambdas/nfl/conftest.py for
# the identical pattern.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaafb", "predict"))
_load_handler("ncaafb_predict", "aws-lambdas/ncaafb/predict/handler.py")

# predict-read/'s handler.py only ever imports from library.* (no local
# sibling modules the way predict/'s model_loader.py etc. are) -- no
# sys.path insert needed, just the same unique-module-name registration.
_load_handler("ncaafb_predict_read", "aws-lambdas/ncaafb/predict-read/handler.py")

# live-scores/'s own live_scores.py has a unique name, unlike handler.py --
# same pattern as ingest/'s enrichment.py above. See NFL's own
# aws-lambdas/nfl/conftest.py for the identical pattern.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaafb", "live-scores"))
_load_handler("ncaafb_live_scores", "aws-lambdas/ncaafb/live-scores/handler.py")


@pytest.fixture(autouse=True)
def reset_ncaafb_predict_singletons():
    """Clears ncaafb_predict's own module-level singletons before and
    after every test in this directory -- same reasoning and shape as
    NFL's own reset_nfl_predict_singletons fixture."""
    ncaafb_predict = sys.modules.get("ncaafb_predict")
    if ncaafb_predict is None:
        yield
        return
    ncaafb_predict._storage = None
    ncaafb_predict._model_bucket = None
    ncaafb_predict._predictions_table = None
    yield
    ncaafb_predict._storage = None
    ncaafb_predict._model_bucket = None
    ncaafb_predict._predictions_table = None


@pytest.fixture(autouse=True)
def reset_ncaafb_predict_read_singletons():
    ncaafb_predict_read = sys.modules.get("ncaafb_predict_read")
    if ncaafb_predict_read is None:
        yield
        return
    ncaafb_predict_read._storage = None
    ncaafb_predict_read._model_bucket = None
    ncaafb_predict_read._predictions_table = None
    ncaafb_predict_read._predict_invoker = None
    yield
    ncaafb_predict_read._storage = None
    ncaafb_predict_read._model_bucket = None
    ncaafb_predict_read._predictions_table = None
    ncaafb_predict_read._predict_invoker = None
