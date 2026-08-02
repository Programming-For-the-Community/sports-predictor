"""
Assembles a live feature vector for one not-yet-played NFL event or one
player-prop target, using the exact same pure functions
(build_event_features/build_player_features, library/features/nfl.py)
that build the training datasets -- this is the "later" half of the
train/serve-skew guarantee those functions' own docstrings describe.

The difference from feature-engineering's build_dataset.py is entirely
in how history gets gathered: batch feature engineering loads the whole
table once and walks it incrementally (cheap when you're building
thousands of rows); this module looks up exactly one event's worth of
context via FeatureStorage's one-team/one-player/one-event methods
(get_team_events, get_player_game_stats, get_player_game_stats_for_event,
get_team_game_stats_for_team, get_event, get_entity) -- cheap when you're
only ever building one row per request. Recomputes Elo from the full
completed-events history on every call rather than caching a "current
rating" snapshot -- NFL's ~2,700 completed games is the same cost
build_dataset.py already treats as cheap-enough-to-scan, and this project
has no request volume that would make that recomputation a real cost.
"""
from library.features.nfl import (
    DEFAULT_STARTING_RATING,
    build_event_features,
    build_player_features,
    compute_elo_ratings,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
)
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5


class EventNotFoundError(Exception):
    pass


class MalformedEventError(Exception):
    """The event exists but is missing a home/away participant role --
    build_event_features/build_player_features both require both sides
    to determine home/away, opponent, and Elo lookups."""


def _home_away_ids(event: dict) -> tuple[str, str]:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event.get('event_key')} is missing a home or away participant")
    return home["entity_id"], away["entity_id"]


def _live_elo_ratings(storage, sport: str, event: dict, home_id: str, away_id: str) -> dict:
    """A minimal elo_ratings dict containing just this one (not-yet-played)
    event's key, mapped to each team's CURRENT rating -- the second
    compute_elo_ratings return value, not the first. The first
    (pre_game_ratings) only has entries for events already processed, so
    it has nothing for a future event's own key; build_event_features/
    build_player_features only ever do one lookup
    (elo_ratings.get(event['event_key'])), so a single-entry dict works
    identically to the full training-time one for this purpose."""
    completed_events = storage.get_all_events(sport)
    _, current_ratings = compute_elo_ratings(completed_events)
    return {
        event["event_key"]: {
            "home_pre_rating": current_ratings.get(home_id, DEFAULT_STARTING_RATING),
            "away_pre_rating": current_ratings.get(away_id, DEFAULT_STARTING_RATING),
        }
    }


def _presumptive_leader_and_history(
    storage, sport: str, team_id: str, before_date: str, identify_fn, window: int,
) -> list[dict]:
    """A future game has no player_game_stats of its own yet to identify
    a starting QB/lead rusher/lead receiver from (identify_starting_qb et
    al. all work by finding whoever had the most volume IN a given game).
    Approximates "who's likely to play that role next" as "whoever held
    it last game" -- pulls that team's most recent completed event, the
    stat lines from it, identifies the leader the same way training does,
    then returns that player's own rolling history. Empty list (not None)
    when no prior event or no identifiable leader exists, matching
    build_event_features' own home_qb_games=None-or-list contract."""
    last_events = storage.get_team_events(sport, team_id, before_date=before_date, limit=1)
    if not last_events:
        return []
    last_event_players = storage.get_player_game_stats_for_event(last_events[0]["event_key"])
    team_players = [row for row in last_event_players if row.get("team_id") == team_id]
    leader = identify_fn(team_players)
    if leader is None:
        return []
    return storage.get_player_game_stats(leader["entity_id"], before_date=before_date, limit=window)


def build_live_event_features(storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW) -> dict:
    """One event-level feature row for event_key, in the exact shape
    train_model.py/train_score_model.py were trained on (label_* fields
    will be None/absent for an unplayed game -- callers only read the
    feature columns off the result, never the labels)."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    home_events = storage.get_team_events(sport, home_id, before_date=event["event_date"], limit=window)
    away_events = storage.get_team_events(sport, away_id, before_date=event["event_date"], limit=window)
    home_box = storage.get_team_game_stats_for_team(home_id, before_date=event["event_date"], limit=window)
    away_box = storage.get_team_game_stats_for_team(away_id, before_date=event["event_date"], limit=window)

    return build_event_features(
        event,
        _live_elo_ratings(storage, sport, event, home_id, away_id),
        home_events,
        away_events,
        window,
        home_qb_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], identify_starting_qb, window),
        away_qb_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], identify_starting_qb, window),
        home_rb_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], identify_lead_rusher, window),
        away_rb_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], identify_lead_rusher, window),
        home_wr_games=_presumptive_leader_and_history(
            storage, sport, home_id, event["event_date"], identify_lead_receiver, window),
        away_wr_games=_presumptive_leader_and_history(
            storage, sport, away_id, event["event_date"], identify_lead_receiver, window),
        home_team_box_stats=home_box,
        away_team_box_stats=away_box,
    )


def build_live_player_features(
    storage, sport: str, event_key: str, entity_id: str, window: int = DEFAULT_ROLLING_WINDOW,
) -> dict:
    """One player-prop feature row for entity_id's performance in
    event_key, in the exact shape train_player_prop_model.py was trained
    on. team_id comes from the player's own entity record (metadata.team_id,
    kept current by every normalize.py upsert), not from the event or
    their own last game log, since a just-traded player's most recent
    game log would still show their old team."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    entity = storage.get_entity(sport, entity_id)
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = entity.get("metadata", {}).get("team_id")

    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    own_previous_events = storage.get_team_events(sport, team_id, before_date=event["event_date"], limit=1)
    own_previous_event_date = own_previous_events[0]["event_date"] if own_previous_events else None

    player_game = {
        "event_key": event["event_key"],
        "player_key": player_key(sport, entity_id),
        "entity_id": entity_id,
        "team_id": team_id,
        "event_date": event["event_date"],
        "stat_line": {},  # unknown -- this is what's being predicted, not labeled
    }

    return build_player_features(
        player_game,
        prior_games,
        event,
        _live_elo_ratings(storage, sport, event, home_id, away_id),
        own_previous_event_date,
        window,
    )
