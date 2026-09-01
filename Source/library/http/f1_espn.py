"""
ESPN client for F1's own live-scores Lambda ONLY -- library/http/f1.py's
JolpicaClient remains this sport's real historical/backfill data source
(ingest, normalize, backfill, feature engineering all stay Jolpica-only,
untouched by this addition). ESPN is used here purely because Jolpica has
no live-timing data at all -- a round's own /results call returns an
empty Results list until the session is fully over (see project-f1-
onboarding memory). See aws-lambdas/f1/live-scores/live_scores.py's own
docstring for the full reasoning this was built from.
"""
from library.http.espn import EspnBaseClient


class F1EspnClient(EspnBaseClient):
    def __init__(self, min_interval_seconds: float = 0.3):
        super().__init__(sport_path="racing/f1", min_interval_seconds=min_interval_seconds)

    def get_scoreboard(self, season: int) -> dict:
        """The full season's races in ONE call -- confirmed live
        2026-08-31: `dates={season}` returns every event for that year
        (~24-25, including a genuinely canceled one, confirmed against a
        real 2026 Bahrain/Saudi Arabia cancellation), not just the current
        window. Each event (race weekend) carries its own list of REAL
        per-session competitions (FP1/FP2/FP3/Qual/Race, and Sprint/Sprint
        Qual on a Sprint weekend) -- unlike every other ESPN sport this
        project uses, one F1 "event" is FIVE-OR-SEVEN real competitions,
        not one. Each competition has its own status.type.state
        ("pre"/"in"/"post") and its own competitors[] list, each entry
        carrying a live running `order` (confirmed live against a real
        completed race: order 1 == the actual race winner) plus a
        `winner` flag and the driver's own athlete name -- no team/
        constructor field, no gap/lap-time/points detail at this level.

        No separate leaderboard/summary endpoint works for F1 -- confirmed
        live 2026-08-31, both 404 (racing/f1/leaderboard, racing/f1/
        summary -- the latter's own internal implementation reuses the
        event id AS a competition id, which breaks the instant an event
        genuinely has more than one competition, a real ESPN API
        limitation for this sport, not a bug on this project's side).
        This one call is therefore the ONLY usable source; live_scores.py
        fetches it directly every tick rather than discovering candidates
        and fetching per-event the way PGA's own live-scores does."""
        return self._get("scoreboard", params={"dates": season})
