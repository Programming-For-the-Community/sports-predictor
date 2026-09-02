"""
NCAA MBB ESPN client. Extends EspnBaseClient with the
basketball/mens-college-basketball sport path.

Same shape as NBAClient -- no depth-chart method (basketball has no
position-ranked depth-chart concept), and no bulk box-score-by-date
endpoint exists -- get_summary is strictly per-game, same as every other
sport.

Unlike NBA's client, both get_teams and get_scoreboard_for_date need an
explicit extra param: with 30 teams and NBA's own game-per-night volume,
NBA's client never needed either fix, so this class is not a blind copy
of NBAClient's shape despite looking similar.
"""
from library.http.espn import EspnBaseClient


class NCAAMBBClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="basketball/mens-college-basketball", min_interval_seconds=min_interval_seconds)

    def get_teams(self) -> dict:
        """Every D1 team -- ESPN's default page size is 50 with no `limit`
        param, silently truncating D1's real ~362 teams to the first 50.
        No pagination metadata (count/pageIndex/pageSize/pageCount) is
        present anywhere in this response shape to detect truncation or
        drive real paging, so 1000 is a fixed, generously-padded ceiling
        rather than a computed value."""
        return self._get("teams", params={"limit": 1000})

    def get_scoreboard_for_date(self, date: str) -> dict:
        """Fetch the day's full scoreboard (YYYYMMDD) -- ESPN infers
        season/season-type entirely from this one parameter, same as
        NBAClient.get_scoreboard_for_date. No preseason games appear on
        this scoreboard at all (first games of a season are already
        season.type=2/regular-season), unlike NFL/NBA -- so callers here
        have no preseason-type filter to apply.

        groups=50 (ESPN's id for "NCAA Division I") is required, not
        optional: a bare `dates`-only call silently returns some
        unscoped/"featured" subset of the day's games (as few as ~10% of
        a full slate on a busy date), not the full D1 schedule. NBA's own
        client never needed this because NBA's single-league scoreboard
        has no equivalent scoping ambiguity.

        limit=400 is defensive padding -- groups=50 alone already returns
        the correct full count (up to 169 events)."""
        return self._get("scoreboard", params={"dates": date, "groups": 50, "limit": 400})

    def get_summary(self, event_id: str) -> dict:
        return self._get("summary", params={"event": event_id})

    def get_current_rankings_pointer(self) -> dict:
        """The site API's own /rankings response -- current-only, no
        historical season/week query support (season/week params are
        silently ignored). Shaped
        {"rankings": [{"id", "name", "type", "$ref", "occurrence"}, ...]},
        one entry per poll (AP, Coaches, ...) -- callers use
        library.http.ncaambb_core.current_ap_poll_pointer to pick the AP
        one and resolve it into a (season, season_type, week) tuple for
        NCAAMBBCoreClient.get_ap_poll, since this response's own `$ref`
        points at an internal-only hostname that doesn't resolve
        publicly (see that module's own docstring)."""
        return self._get("rankings", params={})

    def get_roster(self, team_id: str) -> dict:
        """One team's full current roster. Same flat (ungrouped) athletes
        list as NBAClient.get_roster, each athlete carrying its own
        `position`/`injuries`; the response also carries a top-level
        `coach` field. No pagination concern here -- a team's roster is
        at most ~15-20 players, far under ESPN's default page size."""
        return self._get(f"teams/{team_id}/roster", params={})
