"""
NFL-specific endpoint methods on top of library.http.espn.EspnBaseClient,
which supplies the shared ESPN root URL, retry/backoff, and rate
limiting. Only the "football/nfl" path suffix and the three endpoint
methods below are NFL-specific -- everything else is shared with any
other sport's ESPN-backed client.
"""
from library.http.espn import EspnBaseClient


class NFLClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="football/nfl", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        return self._get("teams", params={})

    def get_scoreboard(self, year: int, seasontype: int, week: int) -> dict:
        return self._get("scoreboard", params={"dates": year, "seasontype": seasontype, "week": week})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})
