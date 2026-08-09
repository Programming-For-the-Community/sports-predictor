"""
Generic helpers for turning JSON API responses (camelCase keys,
stringified numbers -- a pattern shared by ESPN, CFBD, and most other
sport data APIs) into the snake_case, typed values this project's schema
uses.
"""
import re

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def snake_case(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


def parse_number(value):
    if not isinstance(value, str):
        return value
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value  # not numeric (e.g. "--") -- keep as-is rather than guess


def parse_clock_to_seconds(display_value):
    """Converts a "MM:SS" time-of-possession string (e.g. "23:45") into
    total seconds -- the same format both ESPN's and CFBD's team box
    scores use for possession time. Falls back to the raw value unparsed
    if it's not clock-shaped."""
    if not isinstance(display_value, str) or ":" not in display_value:
        return display_value
    minutes, _, seconds = display_value.partition(":")
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return display_value
