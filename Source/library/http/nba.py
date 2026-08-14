"""
NBA ESPN client. Extends EspnBaseClient with the basketball/nba sport path.

No depth-chart method, unlike NFLClient -- basketball has no equivalent
concept (position-ranked QB1/RB1-style depth), so there's nothing to
mirror there. No bulk box-score-by-date endpoint exists (confirmed live,
2026-08-13, see project-nba-onboarding memory) -- get_summary is strictly
per-game, same as NFL's.
"""
from library.http.espn import EspnBaseClient


class NBAClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="basketball/nba", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        return self._get("teams", params={})

    def get_scoreboard_for_date(self, date: str) -> dict:
        """Fetch the day's full scoreboard (YYYYMMDD) -- ESPN infers
        season/season-type entirely from this one parameter, same as
        NFLClient.get_scoreboard_for_date. Unlike NFL's week-shaped
        schedule, NBA has no separate per-week lookup -- every ingest call
        is date-based, one bulk call per night."""
        return self._get("scoreboard", params={"dates": date})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})

    def get_roster(self, team_id: str) -> dict:
        """One team's full current roster -- confirmed live, 2026-08-14.
        Unlike NFLClient.get_roster's `athletes` (grouped by position group:
        offense/defense/specialTeam/...), NBA's `athletes` is a FLAT list of
        player objects (no grouping) -- normalize must not assume NFL's
        nested shape. Each athlete carries its own `position`, `injuries`,
        and `contracts`; the response also carries a top-level `coach`
        object -- both come along for free on every roster fetch, no
        separate injury-report or coach-lookup call needed the way NFL's
        enrichment.py requires."""
        return self._get(f"teams/{team_id}/roster", params={})
