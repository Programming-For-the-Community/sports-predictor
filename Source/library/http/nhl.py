"""
NHL ESPN client. Extends EspnBaseClient with the hockey/nhl sport path.

Same four endpoints as NBAClient. The scoreboard carries each team's
probable starting goalie; get_summary's box score and play-by-play are
strictly per-game.
"""
from library.http.espn import EspnBaseClient


class NHLClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="hockey/nhl", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        return self._get("teams", params={})

    def get_scoreboard_for_date(self, date: str) -> dict:
        """The day's full scoreboard (YYYYMMDD). ESPN infers season and
        season type from the date."""
        return self._get("scoreboard", params={"dates": date})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})

    def get_roster(self, team_id: str) -> dict:
        """One team's current roster. `athletes` is grouped by position
        group (Centers/Left Wings/Right Wings/Defense/Goalies), each with
        its own `items` list; every athlete carries its own `injuries`."""
        return self._get(f"teams/{team_id}/roster", params={})
