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
