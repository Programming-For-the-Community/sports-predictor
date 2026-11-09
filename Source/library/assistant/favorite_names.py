"""
Turns the team or competitor a user names into the value the app shows, by
matching it against the sport's season standings (get_season_projection,
which lists every team with its name and the app's abbreviation, or every
golfer or driver): "Minnesota Gophers" in NCAAFB is MINN, never the
Vikings' MIN. A sport with no standings yet keeps the name as given, for
user_store to check its form.
"""
from __future__ import annotations

from typing import Callable

from library.assistant.user_store import FavoriteError

ToolInvoker = Callable[[str, dict], dict]
STANDINGS = ("standings", "driver_standings")
MAX_CHOICES = 6


def _rows(invoke_tool: ToolInvoker, sport: str, team: str) -> list[dict] | None:
    """Standings rows naming `team`, or None when the sport has no standings."""
    outcome = invoke_tool("get_season_projection", {"sport": sport, "team": team})
    result = outcome.get("result") or {}
    if not outcome.get("ok") or not any(key in result for key in STANDINGS):
        return None
    return [row for key in STANDINGS for row in (result.get(key) or {}).get("rows") or []]


def resolve(invoke_tool: ToolInvoker, sport: str, team: str, field_sport: bool) -> str:
    """The app's abbreviation (team sports) or full name (field sports)."""
    rows = _rows(invoke_tool, sport, team)
    if rows is None:
        return team
    key = "name" if field_sport else "abbreviation"
    values = list(dict.fromkeys(row[key] for row in rows if row.get(key)))
    exact = [row[key] for row in rows if (row.get("name") or "").lower() == team.strip().lower() or (row.get(key) or "").lower() == team.strip().lower()]
    if len(set(exact)) == 1:
        return exact[0]
    if len(values) == 1:
        return values[0]
    if not values:
        raise FavoriteError(f"No {sport} {'player' if field_sport else 'team'} matches {team!r}. Use the name the user gave, e.g. Minnesota Golden Gophers.")
    choices = ", ".join(f"{row.get('name')} ({row.get('abbreviation')})" if not field_sport else str(row.get("name")) for row in rows[:MAX_CHOICES])
    raise FavoriteError(f"{team!r} matches more than one: {choices}. Ask the user which, then save that one.")
