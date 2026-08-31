import importlib.util
import os
import sys

# RAW_BUCKET_NAME is read at module level by ingest's own handler -- set
# it before loading the module so the import doesn't raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so ingest and
    normalize can coexist in the same pytest session without the generic
    'handler' name colliding in sys.modules -- same pattern
    tests/aws-lambdas/pga/conftest.py already uses. Import failures are
    swallowed rather than raised."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


_load_handler("f1_ingest", "aws-lambdas/f1/ingest/handler.py")
_load_handler("f1_normalize", "aws-lambdas/f1/normalize/handler.py")

# predict/'s own modules (live_features.py, model_loader.py,
# event_prediction.py, season_projection.py, season_simulation.py) have
# unique names -- a plain sys.path entry is enough for them, no
# _load_handler renaming trick needed. predict/handler.py itself still
# needs one, same reasoning as ingest/normalize above -- same pattern
# tests/aws-lambdas/pga/conftest.py already uses.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "f1", "predict"))
_load_handler("f1_predict", "aws-lambdas/f1/predict/handler.py")

# predict-read/'s handler.py only ever imports from library.* (no local
# sibling modules the way predict/'s model_loader.py etc. are) -- no
# sys.path insert needed, just the same unique-module-name registration.
_load_handler("f1_predict_read", "aws-lambdas/f1/predict-read/handler.py")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def reset_f1_predict_singletons():
    """Clears f1_predict's own module-level singletons before and after
    every test in this directory."""
    f1_predict = sys.modules.get("f1_predict")
    if f1_predict is None:
        yield
        return
    f1_predict._storage = None
    f1_predict._model_bucket = None
    f1_predict._predictions_table = None
    yield
    f1_predict._storage = None
    f1_predict._model_bucket = None
    f1_predict._predictions_table = None


@pytest.fixture(autouse=True)
def reset_f1_predict_read_singletons():
    f1_predict_read = sys.modules.get("f1_predict_read")
    if f1_predict_read is None:
        yield
        return
    f1_predict_read._storage = None
    f1_predict_read._model_bucket = None
    f1_predict_read._predictions_table = None
    f1_predict_read._predict_invoker = None
    yield
    f1_predict_read._storage = None
    f1_predict_read._model_bucket = None
    f1_predict_read._predictions_table = None
    f1_predict_read._predict_invoker = None
