"""
Win-model accuracy by conference for a head-to-head sport's scorecard, from
the same graded games as the rest of it (each game's logged pre-event
prediction against its final score). A game counts once for each team's
conference, once when both share one, and games between two teams of the
same conference are also totalled on their own. A sport whose teams carry
no conference (NFL) gets no breakdown.
"""
from __future__ import annotations

from collections import defaultdict

from library.serving.common import (
    WIN_PROBABILITY_MODEL,
    _home_and_away,
    _prediction_comparison,
)

# Fewer graded games than this and a conference's accuracy says little.
MIN_GAMES = 3


def _conference(entities: dict, team_id: str) -> str | None:
    entity = entities.get((team_id, "team")) or {}
    return (entity.get("metadata") or {}).get("conference")


def _row(name: str, games: int, correct: int) -> dict:
    return {"conference": name, "games": games, "correct": correct, "accuracy_pct": round(100 * correct / games, 1)}


def team_refs(events: list[dict]) -> list[tuple[str, str]]:
    """The (entity_id, "team") keys to read conferences for."""
    return sorted({(p["entity_id"], "team") for e in events for p in e.get("participants") or [] if p.get("entity_id")})


def _graded(event: dict, rows_by_event: dict[str, list[dict]], entities: dict) -> tuple[list[str | None], int] | None:
    """([home conference, away conference], 1 if the pick was right else 0),
    or None for a game with no graded prediction."""
    teams = _home_and_away(event)
    comparison = _prediction_comparison(rows_by_event.get(event["event_key"]) or [], event)
    if teams is None or comparison is None:
        return None
    return [_conference(entities, team) for team in teams], int(comparison["correct"])


def by_conference(events: list[dict], rows_by_event: dict[str, list[dict]], entities: dict) -> dict | None:
    """{"model", "conferences": [...], "conference_games": {...}}, busiest
    conference first, or None when no team has a conference."""
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    same = [0, 0]
    for game in filter(None, (_graded(e, rows_by_event, entities) for e in events)):
        pair, hit = game
        for name in {c for c in pair if c}:
            tally[name][0] += 1
            tally[name][1] += hit
        if pair[0] and pair[0] == pair[1]:
            same[0] += 1
            same[1] += hit
    rows = [_row(name, games, correct) for name, (games, correct) in tally.items() if games >= MIN_GAMES]
    if not rows:
        return None
    rows.sort(key=lambda r: (-r["games"], r["conference"]))
    return {
        "model": WIN_PROBABILITY_MODEL,
        "min_games": MIN_GAMES,
        "conferences": rows,
        "conference_games": _row("Conference games", *same) if same[0] else None,
    }
