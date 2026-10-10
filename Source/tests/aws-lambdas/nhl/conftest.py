import importlib.util
import os
import sys

import pytest

# RAW_BUCKET_NAME is read at module level by the ingest handler -- set it
# before loading the module so the import doesn't raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so every
    handler can coexist in one pytest session without the generic
    'handler' name colliding in sys.modules. Import failures are
    swallowed rather than raised."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


_load_handler("nhl_ingest", "aws-lambdas/nhl/ingest/handler.py")
_load_handler("nhl_normalize", "aws-lambdas/nhl/normalize/handler.py")
_load_handler("nhl_schedule_sync", "aws-lambdas/nhl/schedule-sync/handler.py")

# predict/'s own modules (live_features.py, event_prediction.py) have
# unique names within this directory's session, so a sys.path entry is
# enough for them; handler.py needs the renaming above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nhl", "predict"))
_load_handler("nhl_predict", "aws-lambdas/nhl/predict/handler.py")

# live-scores/'s own live_scores.py has a unique name too.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nhl", "live-scores"))
_load_handler("nhl_live_scores", "aws-lambdas/nhl/live-scores/handler.py")


@pytest.fixture(autouse=True)
def _reset_nhl_singletons(reset_singletons):
    reset_singletons(sys.modules.get("nhl_normalize"), _storage=None)
    reset_singletons(sys.modules.get("nhl_live_scores"), _storage=None)
    reset_singletons(sys.modules.get("nhl_predict"), _storage=None, _model_bucket=None, _predictions_table=None)
