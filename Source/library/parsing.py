"""
Generic helpers for turning JSON API responses (camelCase keys,
stringified numbers -- a pattern shared by ESPN, CFBD, and most other
sport data APIs) into the snake_case, typed values this project's schema
uses.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")

# ESPN and CFBD both report/bucket every event under the U.S.-Eastern
# calendar date it's actually played on, not UTC -- a 00:00 UTC kickoff is
# 8pm Eastern the day before. This project's own event_date fields are
# meant to be that same real-world game day, so every normalizer derives
# it through US_EASTERN_TZ rather than truncating a raw UTC timestamp
# (event["date"][:10]/game["startDate"][:10]), which is off by one day
# for any event that starts at/after 8pm Eastern.
US_EASTERN_TZ = ZoneInfo("America/New_York")


def us_eastern_date(moment: datetime) -> str:
    """Dashed-ISO (YYYY-MM-DD) calendar date for `moment` (must be
    timezone-aware) in US_EASTERN_TZ -- this project's event_date fields'
    own basis."""
    return moment.astimezone(US_EASTERN_TZ).date().isoformat()


def us_eastern_date_from_iso(timestamp: str) -> str:
    """us_eastern_date, for a raw ESPN/CFBD UTC timestamp string (e.g.
    "2026-09-04T00:00Z") straight off a JSON response -- what every
    normalizer should call instead of timestamp[:10]."""
    return us_eastern_date(datetime.fromisoformat(timestamp.replace("Z", "+00:00")))


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
