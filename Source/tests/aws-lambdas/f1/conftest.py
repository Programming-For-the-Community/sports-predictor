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

# predict/'s own modules (live_features.py, event_prediction.py,
# season_projection.py, season_simulation.py) have unique names -- a
# plain sys.path entry is enough for them, no _load_handler renaming
# trick needed. predict/handler.py itself still needs one, same reasoning
# as ingest/normalize above -- same pattern tests/aws-lambdas/pga/
# conftest.py already uses.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "f1", "predict"))
_load_handler("f1_predict", "aws-lambdas/f1/predict/handler.py")

# live-scores/'s own live_scores.py has a unique name -- same split as
# predict/'s modules above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "f1", "live-scores"))
_load_handler("f1_live_scores", "aws-lambdas/f1/live-scores/handler.py")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_f1_singletons(reset_singletons):
    """Every f1_* handler module registered above has its own
    module-level singleton cache (FeatureStorage/S3Manager/DynamoDBTable)
    -- reset before/after every test in this directory via
    Source/tests/conftest.py's own reset_singletons, regardless of which
    handler a given test file actually exercises (a no-op for any module
    that test never touches)."""
    reset_singletons(sys.modules.get("f1_predict"), _storage=None, _model_bucket=None, _predictions_table=None)
    reset_singletons(sys.modules.get("f1_normalize"), _storage=None)
    reset_singletons(sys.modules.get("f1_live_scores"), _storage=None)
