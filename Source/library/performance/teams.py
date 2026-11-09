"""
Win-model accuracy per team for a head-to-head sport's scorecard, from the
same graded games as the rest of it: how often the pick was right in games
each team played, over the scorecard's games and over the last RECENT_DAYS.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from library.serving.common import WIN_PROBABILITY_MODEL, _home_and_away, _prediction_comparison

RECENT_DAYS = 30


def _row(entity: dict, season: list[int], recent: list[int]) -> dict:
    metadata = entity.get("metadata") or {}
    row = {"team": metadata.get("abbreviation"), "name": entity.get("name"), "games": season[0], "correct": season[1],
           "accuracy_pct": round(100 * season[1] / season[0], 1)}
    if recent[0]:
        row.update(recent_games=recent[0], recent_correct=recent[1])
    return row


def by_team(events: list[dict], rows_by_event: dict[str, list[dict]], entities: dict, today: date) -> dict | None:
    """{"model", "recent_days", "teams": [...]}, most games first, or None
    when nothing was graded."""
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    season: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    recent: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for event in events:
        teams = _home_and_away(event)
        comparison = _prediction_comparison(rows_by_event.get(event["event_key"]) or [], event)
        if teams is None or comparison is None:
            continue
        hit = int(comparison["correct"])
        for team in teams:
            season[team][0] += 1
            season[team][1] += hit
            if (event.get("event_date") or "") >= cutoff:
                recent[team][0] += 1
                recent[team][1] += hit
    rows = [_row(entities.get((team, "team")) or {}, tally, recent[team]) for team, tally in season.items()]
    if not rows:
        return None
    rows.sort(key=lambda r: (-r["games"], r["name"] or ""))
    return {"model": WIN_PROBABILITY_MODEL, "recent_days": RECENT_DAYS, "teams": rows}
