import importlib.util
import os
import sys

# RAW_BUCKET_NAME is read at module level by the ingest handler -- set it
# before loading either module so the import doesn't raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so ingest,
    normalize, and predict can all coexist in the same pytest session
    without the generic 'handler' name colliding in sys.modules.

    Every CI job in this directory only installs its OWN component's
    requirements.txt (see nfl_data_pipeline.yml/nfl_ai_hosting.yml) --
    ingest's job never has xgboost, predict's job never has requests. A
    failed import here is swallowed rather than raised so that, e.g.,
    running just test_ingest.py doesn't fail purely because predict's
    handler (which imports xgboost at module level via model_loader.py)
    couldn't load in a job that never installed it. Each test file only
    ever imports the one handler module it actually tests, so a module
    that silently failed to register here is never missed by a test that
    doesn't need it."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        return
    sys.modules[module_name] = mod


# ingest/'s own enrichment.py has a unique name, unlike handler.py -- a
# plain sys.path entry is enough for it, same as predict/'s sibling
# modules below.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nfl", "ingest"))
_load_handler("nfl_ingest", "aws-lambdas/nfl/ingest/handler.py")
_load_handler("nfl_normalize", "aws-lambdas/nfl/normalize/handler.py")
_load_handler("nfl_schedule_sync", "aws-lambdas/nfl/schedule-sync/handler.py")

# predict/'s own modules (live_features.py, model_loader.py) have unique
# names, unlike handler.py -- a plain sys.path entry is enough for them,
# no _load_handler renaming trick needed. predict/handler.py itself still
# needs one, same reasoning as ingest/normalize above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nfl", "predict"))
_load_handler("nfl_predict", "aws-lambdas/nfl/predict/handler.py")

# predict-read/'s handler.py only ever imports from library.* (no local
# sibling modules the way predict/'s model_loader.py etc. are) -- no
# sys.path insert needed, just the same unique-module-name registration.
_load_handler("nfl_predict_read", "aws-lambdas/nfl/predict-read/handler.py")

# live-scores/'s own live_scores.py has a unique name, unlike handler.py --
# same pattern as ingest/'s enrichment.py above.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "nfl", "live-scores"))
_load_handler("nfl_live_scores", "aws-lambdas/nfl/live-scores/handler.py")