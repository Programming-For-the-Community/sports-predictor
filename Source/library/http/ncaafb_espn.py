"""
Small ESPN site-API client for NCAAFB live scores only -- NOT the sport's
primary data source (that's CFBDClient, library/http/cfbd.py). Exists
purely so live-scores/live_scores.py can poll ESPN's free college-football
scoreboard at zero CFBD quota cost (see design/DATA_SOURCES.md's NCAA FB
row: "live scores instead come from the free ESPN scoreboard supplement").
"""
from library.http.espn import EspnBaseClient


class NcaafbEspnClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="football/college-football", min_interval_seconds=min_interval_seconds)

    def get_scoreboard_for_date(self, date: str) -> dict:
        """Fetch the scoreboard for the date (YYYYMMDD) -- same single-
        parameter shape as NFLClient.get_scoreboard_for_date (library/
        http/nfl.py); ESPN infers season/week/groups entirely from
        `dates`. groups=80 (FBS) isn't passed -- the unfiltered scoreboard
        already includes every game the ESPN site itself shows for that
        date, and live_scores.py's own matching only ever looks up
        specific candidates from our own event history, so extra
        non-FBS games in the response are simply never matched against."""
        return self._get("scoreboard", params={"dates": date})
