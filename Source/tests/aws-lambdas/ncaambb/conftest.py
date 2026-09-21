import importlib.util
import os
import sys

import pytest

# RAW_BUCKET_NAME/ENTITIES_TABLE_NAME are read at module level by
# ingest/schedule-sync's own handlers -- set them before loading either
# module so the import doesn't raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")
os.environ.setdefault("ENTITIES_TABLE_NAME", "test-entities-table")

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


_load_handler("ncaambb_ingest", "aws-lambdas/ncaambb/ingest/handler.py")
_load_handler("ncaambb_normalize", "aws-lambdas/ncaambb/normalize/handler.py")
_load_handler("ncaambb_schedule_sync", "aws-lambdas/ncaambb/schedule-sync/handler.py")

# predict/'s own modules (live_features.py, event_prediction.py) have
# unique names -- a plain sys.path entry is enough for them, no
# _load_handler renaming trick needed. predict/handler.py itself still
# needs one, same reasoning as ingest/normalize above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaambb", "predict"))
_load_handler("ncaambb_predict", "aws-lambdas/ncaambb/predict/handler.py")

# live-scores/'s own live_scores.py has a unique name -- same split as
# predict/'s modules above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaambb", "live-scores"))
_load_handler("ncaambb_live_scores", "aws-lambdas/ncaambb/live-scores/handler.py")


@pytest.fixture(autouse=True)
def _reset_ncaambb_singletons(reset_singletons):
    """Every ncaambb_* handler module registered above has its own
    module-level singleton cache (FeatureStorage/S3Manager/DynamoDBTable/
    a raw-bucket handle) -- reset before/after every test in this
    directory via Source/tests/conftest.py's own reset_singletons,
    regardless of which handler a given test file actually exercises (a
    no-op for any module that test never touches)."""
    reset_singletons(
        sys.modules.get("ncaambb_predict"),
        _storage=None, _model_bucket=None, _predictions_table=None, _raw_bucket=None,
    )
    reset_singletons(sys.modules.get("ncaambb_normalize"), _storage=None)
    reset_singletons(sys.modules.get("ncaambb_live_scores"), _storage=None)
