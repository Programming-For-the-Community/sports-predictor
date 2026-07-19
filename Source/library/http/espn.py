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

from library.http.client import HttpClient

DEFAULT_ESPN_API_ROOT_URL = "https://site.api.espn.com/apis/site/v2/sports"


def _espn_root_url() -> str:
    return os.environ.get("ESPN_API_ROOT_URL", DEFAULT_ESPN_API_ROOT_URL).rstrip("/")


class EspnBaseClient(HttpClient):
    def __init__(self, sport_path: str, min_interval_seconds: float = 0.3):
        base_url = f"{_espn_root_url()}/{sport_path.strip('/')}"
        super().__init__(base_url=base_url, min_interval_seconds=min_interval_seconds)
