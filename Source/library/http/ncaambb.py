"""
NCAA MBB ESPN client. Extends EspnBaseClient with the
basketball/mens-college-basketball sport path.

Same shape as NBAClient -- no depth-chart method (basketball has no
position-ranked depth-chart concept), and no bulk box-score-by-date
endpoint exists (confirmed live, 2026-08-19, see project-ncaambb-onboarding
memory) -- get_summary is strictly per-game, same as every other sport.
"""
from library.http.espn import EspnBaseClient


class NCAAMBBClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="basketball/mens-college-basketball", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        return self._get("teams", params={})

    def get_scoreboard_for_date(self, date: str) -> dict:
        """Fetch the day's full scoreboard (YYYYMMDD) -- ESPN infers
        season/season-type entirely from this one parameter, same as
        NBAClient.get_scoreboard_for_date. Confirmed live: no preseason
        games appear on this scoreboard at all (first games of a season
        are already season.type=2/regular-season), unlike NFL/NBA -- so
        callers here have no preseason-type filter to apply."""
        return self._get("scoreboard", params={"dates": date})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})

    def get_roster(self, team_id: str) -> dict:
        """One team's full current roster -- confirmed live, 2026-08-19.
        Same flat (ungrouped) athletes list as NBAClient.get_roster, each
        athlete carrying its own `position`/`injuries`; the response also
        carries a top-level `coach` field."""
        return self._get(f"teams/{team_id}/roster", params={})
