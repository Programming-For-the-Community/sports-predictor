"""
Assembles a live feature vector for one NHL event or player-prop target,
using the same pure functions (library/features/nhl.py) that build the
training datasets. A thin layer over library.features.hockey_live, which
owns the storage reads and the starting-goalie resolution.

build_live_event_features also remembers which goalie each side's row
was built for, so event_prediction can report it without rebuilding.
"""
from library.features import hockey_live
from library.features.live_orchestration import EventNotFoundError, MalformedEventError

# The most recently built event row's starters: {event_key: {role: {...}}}.
_resolved_goalies: dict[str, dict] = {}


def build_live_event_features(storage, sport: str, event_key: str, events: list[dict] | None = None) -> dict:
    row = hockey_live.build_live_event_features(storage, sport, event_key, events=events)
    _resolved_goalies.clear()
    _resolved_goalies[event_key] = {
        role: {"entity_id": row[f"{role}_goalie_id"], "source": row[f"{role}_goalie_source"]}
        for role in ("home", "away") if row.get(f"{role}_goalie_id")
    }
    return row


def resolved_goalies(event_key: str) -> dict:
    """{"home": {"entity_id", "source"}, "away": {...}} for the event row
    build_live_event_features last built; a side with no goalie is omitted."""
    return _resolved_goalies.get(event_key, {})


build_live_player_features = hockey_live.build_live_player_features
build_live_event_leader_candidates = hockey_live.build_live_event_leader_candidates
