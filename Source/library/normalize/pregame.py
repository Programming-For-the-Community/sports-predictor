"""
Event fields that describe a game as it stood before kickoff. Ingest
attaches them only while a game hasn't finished, and an event rewrite
without them keeps the values already on file, so a completed event
carries its last pre-game snapshot rather than post-game data.

Injury reports attached that way are stamped with INJURIES_AS_OF; feature
code reads them through pregame_injuries, which ignores an unstamped list
(those were attached after the game, before this rule existed).
"""
from datetime import UTC, datetime, timezone

INJURIES_AS_OF = "injuries_as_of"

_COACH_FIELDS = ("coach_id", "coach_name", "coach_experience", "coach_season_win_pct", "coach_career_playoff_win_pct")

PREGAME_FIELDS = (
    "home_injuries",
    "away_injuries",
    INJURIES_AS_OF,
    "weather_temperature",
    "weather_condition_id",
    *(f"{side}_{field}" for side in ("home", "away") for field in _COACH_FIELDS),
)


def is_completed(raw_event: dict) -> bool:
    """An ESPN scoreboard event's own completed flag."""
    return bool(((raw_event.get("status") or {}).get("type") or {}).get("completed"))


def carry_pregame_fields(item: dict, existing: dict | None) -> dict:
    """item with each pre-game field it lacks taken from the existing event."""
    if not existing:
        return item
    kept = {field: existing[field] for field in PREGAME_FIELDS if item.get(field) is None and existing.get(field) is not None}
    return {**item, **kept} if kept else item


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def pregame_injuries(event: dict, side: str) -> list[dict] | None:
    """The side's pre-game injury report; None when the event has no
    stamped report."""
    return event.get(f"{side}_injuries") if event.get(INJURIES_AS_OF) else None
