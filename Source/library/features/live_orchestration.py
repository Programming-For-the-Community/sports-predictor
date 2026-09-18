"""
Shared event/roster plumbing for nfl/nba/ncaafb/ncaambb's own predict/
live_features.py -- home/away participant extraction, live (in-progress)
Elo lookups, recent-stat-volume ranking, and roster-freshness checking are
identical shapes across all four team sports (module-private helpers there,
made public here since this module is now their shared home). Each sport's
own live_features.py re-exports these directly and keeps its own
build_live_event_features/build_live_player_features/
build_live_event_leader_candidates plus presumptive-leader selection --
those differ genuinely per sport: NFL's depth-chart/injury-based selection
has no equivalent elsewhere, NCAAFB has a geo travel-distance feature the
others don't, and each sport's own roster-staleness day threshold and
`_still_on_team`/`_box_score_candidate_ids` shape (NBA checks team_id only;
NCAAFB/NCAAMBB additionally require a freshness recheck) are kept separate
rather than merged -- see `is_roster_entry_fresh` below, which takes that
threshold as an argument rather than assuming a shared constant.

PGA and F1 are explicitly out of scope -- their live_features.py is a
structurally different shape (per-competitor field iteration, no home/away
concept, multiple event types).
"""
from datetime import date

from library.features.common import DEFAULT_STARTING_RATING, compute_elo_ratings


class EventNotFoundError(Exception):
    pass


class MalformedEventError(Exception):
    """The event exists but is missing a home/away participant role --
    build_event_features/build_player_features both require both sides to
    determine home/away, opponent, and Elo lookups."""


def home_away_ids(event: dict) -> tuple[str, str]:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event.get('event_key')} is missing a home or away participant")
    return home["entity_id"], away["entity_id"]


def live_elo_ratings(
    storage, sport: str, event: dict, home_id: str, away_id: str, current_ratings: dict | None = None,
    events: list[dict] | None = None,
) -> dict:
    """A minimal elo_ratings dict containing just this one (not-yet-played)
    event's key, mapped to each team's CURRENT rating (compute_elo_ratings'
    second return value, not pre_game_ratings, which has no entry for a
    future event).

    current_ratings/events are optional already-computed pass-throughs
    for a caller that's already paid for them."""
    if current_ratings is None:
        completed_events = events if events is not None else storage.get_all_events(sport)
        _, current_ratings = compute_elo_ratings(completed_events, as_of_season=event.get("season"))
    return {
        event["event_key"]: {
            "home_pre_rating": current_ratings.get(home_id, DEFAULT_STARTING_RATING),
            "away_pre_rating": current_ratings.get(away_id, DEFAULT_STARTING_RATING),
        }
    }


def recent_volume(storage, entity_id: str, stat: str, before_date: str, window: int) -> tuple[float, list[dict]]:
    """(sum of stat over a candidate's most recent `window` games, that
    same game list) for one candidate. A candidate with no games at all
    sums to 0 rather than being excluded, so it still competes on equal
    footing but never wins over a candidate with real volume."""
    games = storage.get_player_game_stats(entity_id, before_date=before_date, limit=window)
    total = sum(game.get("stat_line", {}).get(stat, 0) for game in games)
    return total, games


def top_n_by_recent_volume(
    storage, entity_ids: list[str], stat: str, before_date: str, window: int, n: int,
) -> dict[str, list[dict]]:
    """entity_id -> recent game history, for the top n of entity_ids by recent volume of stat."""
    if not entity_ids:
        return {}
    volumes = {entity_id: recent_volume(storage, entity_id, stat, before_date, window) for entity_id in entity_ids}
    ranked = sorted(volumes, key=lambda entity_id: volumes[entity_id][0], reverse=True)[:n]
    return {entity_id: volumes[entity_id][1] for entity_id in ranked}


def team_previous_event_date(
    storage, sport: str, team_id: str, before_date: str, events: list[dict] | None = None,
) -> str | None:
    previous = storage.get_team_events(sport, team_id, before_date=before_date, limit=1, events=events)
    return previous[0]["event_date"] if previous else None


def team_player_games_for_event(storage, team_id: str, event_key: str) -> list[dict]:
    return [row for row in storage.get_player_game_stats_for_event(event_key) if row.get("team_id") == team_id]


def is_roster_entry_fresh(entity: dict, reference_date: str, staleness_days: int) -> bool:
    """reference_date is when this prediction is being generated (today),
    not the target event's own date -- roster sync only ever writes
    "today's" team_id_as_of, so comparing against the event's date would
    make any far-future event fail this check regardless of freshness.
    staleness_days is each sport's own threshold -- deliberately not a
    shared constant, since it varies with how often that sport's own
    roster feed actually refreshes."""
    as_of = (entity.get("metadata") or {}).get("team_id_as_of")
    if as_of is None:
        return False
    try:
        return abs((date.fromisoformat(reference_date) - date.fromisoformat(as_of)).days) <= staleness_days
    except ValueError:
        return False
