"""
NFL ESPN client. Extends EspnBaseClient with the football/nfl sport path
and the four endpoint methods used by both the historical backfill
(data-backfills/nfl/) and the recurring ingest Lambda
(aws-lambdas/nfl/ingest/).
"""
from library.http.espn import EspnBaseClient


class NFLClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="football/nfl", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        return self._get("teams", params={})

    def get_scoreboard(self, year: int, seasontype: int, week: int) -> dict:
        return self._get("scoreboard", params={"dates": year, "seasontype": seasontype, "week": week})

    def get_scoreboard_for_date(self, date: str) -> dict:
        """Fetch the scoreboard for the NFL week containing a specific date
        (YYYYMMDD). ESPN infers season/season_type/week entirely from this
        one parameter -- no separate season/seasontype param is needed
        alongside it."""
        return self._get("scoreboard", params={"dates": date})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})

    def get_depth_chart(self, team_id: str) -> dict:
        """One team's current depth chart -- `positions.<code>.athletes[]`,
        ordered by depth rank (starter first). Confirmed live on this same
        site.api.espn.com host/single-response style, NOT on ESPN's other
        "core" API (sports.core.api.espn.com/.../teams/{id}/depthcharts
        404s) -- see library/http/espn_core.py's own docstring for why
        coach/injury data lives on that other client instead of here."""
        return self._get(f"teams/{team_id}/depthcharts", params={})