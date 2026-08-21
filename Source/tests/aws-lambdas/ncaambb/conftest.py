import importlib.util
import os
import sys

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


_load_handler("ncaambb_ingest", "aws-lambdas/ncaambb/ingest/handler.py")
_load_handler("ncaambb_normalize", "aws-lambdas/ncaambb/normalize/handler.py")
_load_handler("ncaambb_schedule_sync", "aws-lambdas/ncaambb/schedule-sync/handler.py")
