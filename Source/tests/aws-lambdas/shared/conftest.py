import importlib.util
import os
import sys

_src = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_handler(module_name: str, relative_path: str) -> None:
    """Same unique-module-name registration trick as tests/aws-lambdas/
    nfl/conftest.py -- no try/except swallow here, unlike that one,
    since season-gate/handler.py only ever imports library.* (stdlib +
    this project's own package), nothing third-party that a slimmer test
    job could plausibly not have installed."""
    path = os.path.join(_src, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[module_name] = mod


_load_handler("shared_season_gate", "aws-lambdas/shared/season-gate/handler.py")
_load_handler("shared_cloudwatch_geo_widget", "aws-lambdas/shared/cloudwatch-geo-widget/handler.py")
