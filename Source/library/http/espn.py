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
from zoneinfo import ZoneInfo

from library.http.client import HttpClient

DEFAULT_ESPN_API_ROOT_URL = "https://site.web.api.espn.com/apis/site/v2/sports"
DEFAULT_ESPN_USER_AGENT = "python-requests/2.31.0"

# ESPN's site API scoreboard endpoints (get_scoreboard_for_date, every
# sport) bucket each event under the U.S. Eastern calendar date its own
# site displays it under, not the UTC date. A 00:00 UTC kickoff is 8pm
# Eastern the day before, and ESPN files it under that earlier date. A
# caller that derives its query date from a UTC "now" instead loses the
# event the moment "now" ticks into the next UTC day -- which, for a
# 6-9pm Eastern kickoff, happens mid-game.
_ESPN_SCOREBOARD_TZ = ZoneInfo("America/New_York")


def espn_scoreboard_date(moment: datetime) -> str:
    """YYYYMMDD for `moment` (must be timezone-aware) in the calendar date
    ESPN's scoreboard bucketing actually uses. Pass this to every sport's
    get_scoreboard_for_date -- never a raw UTC strftime."""
    return moment.astimezone(_ESPN_SCOREBOARD_TZ).strftime("%Y%m%d")


def _espn_root_url() -> str:
    return os.environ.get("ESPN_API_ROOT_URL", DEFAULT_ESPN_API_ROOT_URL).rstrip("/")


def _espn_user_agent() -> str:
    return os.environ.get("ESPN_USER_AGENT", DEFAULT_ESPN_USER_AGENT)


class EspnBaseClient(HttpClient):
    def __init__(self, sport_path: str, min_interval_seconds: float = 0.3):
        base_url = f"{_espn_root_url()}/{sport_path.strip('/')}"
        super().__init__(base_url=base_url, min_interval_seconds=min_interval_seconds, user_agent=_espn_user_agent())
