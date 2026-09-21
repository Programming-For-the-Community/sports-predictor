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
    Import failures are swallowed rather than raised -- the module is
    simply left unregistered."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


# ingest/'s own enrichment.py has a unique name, unlike handler.py -- a
# plain sys.path entry is enough for it.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaafb", "ingest"))
_load_handler("ncaafb_ingest", "aws-lambdas/ncaafb/ingest/handler.py")
_load_handler("ncaafb_normalize", "aws-lambdas/ncaafb/normalize/handler.py")
_load_handler("ncaafb_schedule_sync", "aws-lambdas/ncaafb/schedule-sync/handler.py")

# predict/'s own modules (live_features.py, event_prediction.py) have
# unique names, unlike handler.py -- a plain sys.path entry is enough for
# them, no _load_handler renaming trick needed. predict/handler.py itself
# still needs one, same reasoning as ingest/normalize above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaafb", "predict"))
_load_handler("ncaafb_predict", "aws-lambdas/ncaafb/predict/handler.py")

# live-scores/'s own live_scores.py has a unique name, unlike handler.py --
# same pattern as ingest/'s enrichment.py above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "ncaafb", "live-scores"))
_load_handler("ncaafb_live_scores", "aws-lambdas/ncaafb/live-scores/handler.py")


@pytest.fixture(autouse=True)
def _reset_ncaafb_singletons(reset_singletons):
    """Every ncaafb_* handler module registered above has its own
    module-level singleton cache (FeatureStorage/S3Manager/DynamoDBTable)
    -- reset before/after every test in this directory via
    Source/tests/conftest.py's own reset_singletons, regardless of which
    handler a given test file actually exercises (a no-op for any module
    that test never touches)."""
    reset_singletons(sys.modules.get("ncaafb_predict"), _storage=None, _model_bucket=None, _predictions_table=None)
    reset_singletons(sys.modules.get("ncaafb_normalize"), _storage=None)
    reset_singletons(sys.modules.get("ncaafb_live_scores"), _storage=None)
