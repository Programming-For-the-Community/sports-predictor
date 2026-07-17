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
