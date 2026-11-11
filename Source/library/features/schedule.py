"""
Schedule, travel and body-clock columns for one side of a game, from that
team's own prior completed events (most recent first). Pro sports place a
game at the home team's market (or a known international venue); college
sports place it at the centre of the event's venue_state.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from library.features import us_states
from library.features.common import OFFSEASON_GAP_DAYS, rest_days
from library.features.geo import haversine_km


def _participant(event: dict, team_id: str) -> dict:
    return next((p for p in event.get("participants", []) if p.get("entity_id") == team_id), {})


def _home_id(event: dict) -> str | None:
    return next((p.get("entity_id") for p in event.get("participants", []) if p.get("role") == "home"), None)


def at_home(event: dict, team_id: str) -> bool:
    """Playing in its own building: the home side of a non-neutral-site game."""
    return _participant(event, team_id).get("role") == "home" and not event.get("neutral_site")


@dataclass(frozen=True)
class SiteTable:
    team_coordinates: dict[str, tuple[float, float]] = field(default_factory=dict)
    team_utc_offsets: dict[str, int] = field(default_factory=dict)
    international_venues: dict[str, tuple[float, float]] = field(default_factory=dict)
    by_state: bool = False

    def site(self, event: dict) -> tuple[tuple[float, float] | None, int | None]:
        """(coordinates, standard UTC offset) of where the game is played."""
        if self.by_state:
            state = event.get("venue_state")
            return us_states.coordinates(state), us_states.utc_offset(state)
        abroad = self.international_venues.get(event.get("venue_city"))
        if abroad is not None:
            return abroad, None
        home_id = _home_id(event)
        return self.team_coordinates.get(home_id), self.team_utc_offsets.get(home_id)

    def _home_state(self, team_id: str, events: list[dict]) -> str | None:
        states = Counter(e.get("venue_state") for e in events if at_home(e, team_id) and e.get("venue_state"))
        return states.most_common(1)[0][0] if states else None

    def home(self, team_id: str, events: list[dict]) -> tuple[tuple[float, float] | None, int | None]:
        """(coordinates, standard UTC offset) of the team's own market."""
        if self.by_state:
            state = self._home_state(team_id, events)
            return us_states.coordinates(state), us_states.utc_offset(state)
        return self.team_coordinates.get(team_id), self.team_utc_offsets.get(team_id)


def _kickoff_hour_utc(kickoff_time: str | None) -> int | None:
    if not kickoff_time:
        return None
    try:
        return datetime.fromisoformat(kickoff_time.replace("Z", "+00:00")).hour
    except ValueError:
        return None


def _games_within(event_date: str, team_events: list[dict], days: int) -> int:
    return sum(1 for e in team_events if e.get("event_date") and 0 < rest_days(event_date, e["event_date"]) <= days)


def _consecutive(event_date: str, team_events: list[dict], team_id: str, home: bool) -> int:
    """Games in a row, ending with this one, at home (home) or away from it."""
    count, later = 1, event_date
    for e in team_events:
        if not e.get("event_date") or rest_days(later, e["event_date"]) > OFFSEASON_GAP_DAYS or at_home(e, team_id) != home:
            break
        count += 1
        later = e["event_date"]
    return count


def _distance(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    return haversine_km(a, b) if a is not None and b is not None else None


def clock_columns(event: dict, team_id: str, team_events: list[dict], sites: SiteTable) -> dict:
    """timezone_shift_hours: the venue's clock minus the team's home clock.
    local_start_hour: the start time on the team's home clock."""
    _, site_offset = sites.site(event)
    _, home_offset = sites.home(team_id, [event, *team_events])
    hour = _kickoff_hour_utc(event.get("kickoff_time"))
    return {
        "timezone_shift_hours": site_offset - home_offset if site_offset is not None and home_offset is not None else None,
        "local_start_hour": (hour + home_offset) % 24 if hour is not None and home_offset is not None else None,
    }


def density_columns(event: dict, team_id: str, team_events: list[dict], sites: SiteTable) -> dict:
    """Back-to-backs, games in the last few days, home stand / road trip
    length and the distance from the previous game's site."""
    event_date = event["event_date"]
    previous = team_events[0] if team_events else None
    rest = rest_days(event_date, previous["event_date"]) if previous and previous.get("event_date") else None
    home = at_home(event, team_id)
    in_season = rest is not None and rest <= OFFSEASON_GAP_DAYS
    return {
        "is_back_to_back": None if rest is None else int(rest == 1),
        "games_last_4_days": _games_within(event_date, team_events, 4),
        "games_last_7_days": _games_within(event_date, team_events, 7),
        "home_stand_game_number": _consecutive(event_date, team_events, team_id, True) if home else 0,
        "road_trip_game_number": 0 if home else _consecutive(event_date, team_events, team_id, False),
        "trip_km": _distance(sites.site(previous)[0], sites.site(event)[0]) if in_season else None,
    }


def home_distance_km(event: dict, team_id: str, team_events: list[dict], sites: SiteTable) -> float | None:
    """Distance from the team's own market to the venue."""
    return _distance(sites.home(team_id, [event, *team_events])[0], sites.site(event)[0])


def side_columns(sides: dict[str, dict]) -> dict:
    """{"home": {...}, "away": {...}} as home_/away_ prefixed columns."""
    return {f"{side}_{name}": value for side, columns in sides.items() for name, value in columns.items()}
