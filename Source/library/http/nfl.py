"""
NFL ESPN client. Extends EspnBaseClient with the football/nfl sport path
and the three endpoint methods used by both the historical backfill
(data-backfills/nfl/) and the recurring ingest Lambda (lambdas/nfl/ingest/).
"""
from library.http.espn import EspnBaseClient


class NFLClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="football/nfl", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        return self._get("teams", params={})

    def get_scoreboard(self, year: int, seasontype: int, week: int) -> dict:
        return self._get("scoreboard", params={"dates": year, "seasontype": seasontype, "week": week})

    def get_current_scoreboard(self, year: int, seasontype: int) -> dict:
        """Fetch the current week's scoreboard without specifying a week number."""
        return self._get("scoreboard", params={"dates": year, "seasontype": seasontype})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})