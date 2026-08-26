"""
PGA Tour ESPN client. Unlike every head-to-head sport's client, there is no
per-team-style "teams"/"roster" endpoint to seed player entities from --
golfers only appear as leaderboard competitors, so entity data is derived
from the same leaderboard fetch normalize already needs for event/result
data (see library/normalize/pga.py's leaderboard_event_to_player_entities).

Two distinct ESPN endpoints, confirmed live 2026-08-24 (see
project-pga-onboarding memory): the scoreboard endpoint under this
client's own "golf/pga" path (same shape/purpose as every other sport's
get_scoreboard_for_date -- discovers which event id(s) are active/recently
finished for a given date, plus the full season's tournament calendar via
`response["leagues"][0]["calendar"]`, one call covering the whole season
rather than a per-date walk), and a separate, richer per-event leaderboard
endpoint at a DIFFERENT path (`golf/leaderboard`, no `pga` segment --
that's why this client's sport_path is the bare "golf" root rather than
"golf/pga" the way NBAClient's is "basketball/nba") that alone carries
status.position/earnings -- the actual result data events.participants
needs. The scoreboard fetch exists only to discover which event ids are
current; the leaderboard fetch is where an event's real data comes from.
"""
from library.http.espn import EspnBaseClient


class PGAClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="golf", min_interval_seconds=min_interval_seconds)

    def get_scoreboard_for_date(self, date: str) -> dict:
        """Fetch (YYYYMMDD) -- discovers which tournament id(s) are
        active/recently finished around this date, and (via
        response["leagues"][0]["calendar"]) the full season's tournament
        schedule in the same call. Confirmed live: a date the day after a
        tournament's own endDate still returns that tournament, so daily
        ingest doesn't need to guess exactly when a result becomes final."""
        return self._get("pga/scoreboard", params={"dates": date})

    def get_leaderboard(self, event_id: str) -> dict:
        """One tournament's full leaderboard -- competitor position,
        status (finished/cut/...), score, and earnings. Returns
        {"events": [event]}, a single-element list, same envelope shape
        as get_scoreboard_for_date but always exactly one event since this
        is queried by a specific event_id."""
        return self._get("leaderboard", params={"event": event_id})

    def get_statistics(self) -> dict:
        """Season-to-date per-player statistical leaders (driving
        distance/accuracy, greens in regulation, putts per hole, birdies
        per round, scoring average, etc. -- ~12 categories, confirmed
        live 2026-08-25), a genuinely different endpoint (`pga/statistics`)
        from the tournament-scoped ones above.

        CURRENT-SNAPSHOT-ONLY -- confirmed live that this endpoint's
        `season`/`year` query params are silently ignored (a request with
        either set returns a byte-identical response to one with neither,
        regardless of value). There is no way to retrieve a past season's
        stats retroactively; the only way this project can ever have a
        historical value for one of these categories is by having
        actually captured a snapshot on that date going forward (see
        aws-lambdas/pga/ingest/handler.py). Also top-50-per-category
        only, not the whole field -- a mid-pack golfer may not appear in
        any category at all."""
        return self._get("pga/statistics", params={})
