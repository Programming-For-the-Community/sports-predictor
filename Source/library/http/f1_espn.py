"""
ESPN client for F1's own live-scores Lambda only -- library/http/f1.py's
JolpicaClient remains this sport's historical/backfill data source
(ingest, normalize, backfill, feature engineering all stay Jolpica-only).
ESPN is used here because Jolpica has no live-timing data at all -- a
round's own /results call returns an empty Results list until the
session is fully over.
"""
from library.http.espn import EspnBaseClient


class F1EspnClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="racing/f1", min_interval_seconds=min_interval_seconds)

    def get_scoreboard(self, season: int) -> dict:
        """The full season's races in one call -- `dates={season}` returns
        every event for that year (~24-25, including any canceled ones),
        not just the current window. Each event (race weekend) carries
        its own list of per-session competitions (FP1/FP2/FP3/Qual/Race,
        and Sprint/Sprint Qual on a Sprint weekend) -- unlike every other
        ESPN sport this project uses, one F1 "event" is five-or-seven real
        competitions, not one. Each competition has its own
        status.type.state ("pre"/"in"/"post") and its own competitors[]
        list, each entry carrying a live running `order` (order 1 == the
        race winner) plus a `winner` flag and the driver's own athlete
        name -- no team/constructor field, no gap/lap-time/points detail
        at this level.

        No separate leaderboard/summary endpoint works for F1 (both 404;
        the latter's own internal implementation reuses the event id as a
        competition id, which breaks once an event has more than one
        competition). This one call is therefore the only usable source;
        live_scores.py fetches it directly every tick rather than
        discovering candidates and fetching per-event the way PGA's own
        live-scores does."""
        return self._get("scoreboard", params={"dates": season})
