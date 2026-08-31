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

# predict/'s own season_simulation.py has a unique name -- a plain
# sys.path entry is enough for it, no _load_handler renaming trick
# needed, same convention tests/aws-lambdas/pga/conftest.py uses for its
# own predict/ modules.
sys.path.insert(0, os.path.join(_src, "aws-lambdas", "f1", "predict"))
