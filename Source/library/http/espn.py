"""
Base client for ESPN's public (unofficial) site API, shared across every
sport that uses it -- NFL, NBA, NCAA MBB, and PGA per
design/DATA_SOURCES.md. The root URL is read from ESPN_API_ROOT_URL so a
domain change is a Terraform/env var update, not a code change repeated
across N sports' images. Each sport's subclass supplies only its own path
suffix (e.g. "football/nfl", "basketball/nba") -- that suffix is genuinely
sport-specific and stays in the sport's own client, not here.
"""
import os
from datetime import datetime

from library.http.client import HttpClient
from library.parsing import US_EASTERN_TZ

DEFAULT_ESPN_API_ROOT_URL = "https://site.web.api.espn.com/apis/site/v2/sports"
DEFAULT_ESPN_USER_AGENT = "python-requests/2.31.0"


def espn_scoreboard_date(moment: datetime) -> str:
    """YYYYMMDD for `moment` (must be timezone-aware) in the calendar date
    ESPN's scoreboard bucketing actually uses (see library.parsing's
    US_EASTERN_TZ). Pass this to every sport's get_scoreboard_for_date --
    never a raw UTC strftime. For comparing against a stored event_date
    instead, use library.parsing.us_eastern_date -- same calendar date,
    dashed-ISO rather than this function's query-param format; the two
    must never be compared against each other directly."""
    return moment.astimezone(US_EASTERN_TZ).strftime("%Y%m%d")


def _espn_root_url() -> str:
    return os.environ.get("ESPN_API_ROOT_URL", DEFAULT_ESPN_API_ROOT_URL).rstrip("/")


def _espn_user_agent() -> str:
    return os.environ.get("ESPN_USER_AGENT", DEFAULT_ESPN_USER_AGENT)


class EspnBaseClient(HttpClient):
    def __init__(self, sport_path: str, min_interval_seconds: float = 0.3):
        base_url = f"{_espn_root_url()}/{sport_path.strip('/')}"
        super().__init__(base_url=base_url, min_interval_seconds=min_interval_seconds, user_agent=_espn_user_agent())
