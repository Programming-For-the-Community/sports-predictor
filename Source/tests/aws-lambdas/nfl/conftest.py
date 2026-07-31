import importlib.util
import os
import sys

# RAW_BUCKET_NAME is read at module level by the ingest handler -- set it
# before loading either module so the import doesn't raise KeyError.
os.environ.setdefault("RAW_BUCKET_NAME", "test-bucket")

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Register a handler.py under a unique module name so both ingest and
    normalize can coexist in the same pytest session without the generic
    'handler' name colliding in sys.modules."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)


_load_handler("nfl_ingest", "aws-lambdas/nfl/ingest/handler.py")
_load_handler("nfl_normalize", "aws-lambdas/nfl/normalize/handler.py")