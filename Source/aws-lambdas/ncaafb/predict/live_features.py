"""
Assembles a live feature vector for one not-yet-played NCAAFB event or one
player-prop target, using the exact same pure functions
(build_event_features/build_player_features, library/features/ncaafb.py)
that build the training datasets -- the "later" half of the train/serve-
skew guarantee those functions' own docstrings describe. Same role as
Source/aws-lambdas/nfl/predict/live_features.py, but NOT a port of it --
NCAAFB has no depth-chart or injury data (see library/features/ncaafb.py's
own docstring), so the roster+depth-chart-driven presumptive-leader
selection NFL's own live_features.py uses doesn't apply here at all.

Presumptive QB/RB/WR leader identification: for each team, walks that
team's own completed events (via FeatureStorage.get_team_events, most
recent first) looking for a past game whose box score identifies a
starter (library.features.ncaafb.identify_starting_qb/identify_lead_
rusher/identify_lead_receiver -- the SAME functions
Source/feature-engineering/ncaafb/build_dataset.py uses at train time,
applied here to one specific past game instead of every game), bounded to
the current season plus SEASON_LOOKBACK prior seasons (crosses the season
boundary automatically for a Week 0/1 target with no current-season game
yet -- no special-casing needed, since get_team_events is already sorted
most-recent-first and season only decreases as the walk goes back).

Each candidate found this way is cross-checked against their CURRENT
entity record's own team_id (kept current by roster sync -- see
library/normalize/ncaafb.py's roster_to_player_entities) before being
trusted, so a transfer who's already played for a new team doesn't get
attributed to their old one. KNOWN GAP: this only catches a player who has
already PLAYED for a different team since -- someone who left the program
(graduated, drafted, transferred but hasn't played yet) still shows their
old team_id forever, since nothing ever tells the entities table "this
person is no longer here" the way NFL's own roster-staleness problem
works. Accepted, not silently pretended away.

No sacks leader -- library/features/ncaafb.py has no identify_* function
for defensive_sacks (CFBD's box score has no "who started at pass rusher"
signal the way passing/rushing/receiving attempts identify a starter), so
build_live_event_leaders' result only ever has passing/receiving/rushing
keys, unlike NFL's four-category leaders block.

PERFORMANCE: one event request calls storage.get_team_events up to 10
times (2 for the event-level rolling windows, 6 for the 3-category x
2-team presumptive-leader search below, plus build_live_event_leaders'
own separate walk) -- and FeatureStorage.get_team_events' own docstring
explains why each of those, left un-threaded, re-fetches the sport's
ENTIRE event history from DynamoDB from scratch every time. build_live_
event_features/build_live_event_leaders both take an optional `events`
(an already-fetched get_all_events(sport) result) and thread it through
every downstream get_team_events/_live_elo_ratings call instead -- turning
those 10 full-history queries into 1 when a caller (event_prediction.
predict_event) fetches it once and passes it to both. Omitted, this
still works, just slower (the original per-call behavior) -- this is what
was actually making the live NCAAFB predict route slow enough to risk a
504 against API Gateway's 29s ceiling.
"""
from library.features.common import DEFAULT_STARTING_RATING, compute_elo_ratings
from library.features.ncaafb import (
    build_event_features,
    build_player_features,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
)
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5

# How many seasons before a target event's own season a presumptive-leader
# search is willing to look back -- 1 means "this season, or the
# immediately preceding one" (e.g. last year's bowl game, for a Week 0/1
# target with no current-season game yet). See this module's own
# docstring for why no further special-casing is needed beyond this bound.
SEASON_LOOKBACK = 1

LEADER_IDENTIFIERS = {
    "passing": identify_starting_qb,
    "rushing": identify_lead_rusher,
    "receiving": identify_lead_receiver,
}


class EventNotFoundError(Exception):
    pass


class MalformedEventError(Exception):
    """The event exists but is missing a home/away participant role --
    build_event_features/build_player_features both require both sides to
    determine home/away, opponent, and Elo lookups."""


def _home_away_ids(event: dict) -> tuple[str, str]:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        raise MalformedEventError(f"Event {event.get('event_key')} is missing a home or away participant")
    return home["entity_id"], away["entity_id"]


def _live_elo_ratings(
    storage, sport: str, event: dict, home_id: str, away_id: str, current_ratings: dict | None = None,
    events: list[dict] | None = None,
) -> dict:
    """A minimal elo_ratings dict containing just this one (not-yet-played)
    event's key, mapped to each team's CURRENT rating -- see
    Source/aws-lambdas/nfl/predict/live_features.py's own docstring for
    the full reasoning, identical here. `events` is this module's own
    already-fetched-history pass-through (see this module's own
    docstring) -- used only when current_ratings itself isn't already
    given, to avoid yet another full get_all_events call on top of it."""
    if current_ratings is None:
        completed_events = events if events is not None else storage.get_all_events(sport)
        _, current_ratings = compute_elo_ratings(completed_events, as_of_season=event.get("season"))
    return {
        event["event_key"]: {
            "home_pre_rating": current_ratings.get(home_id, DEFAULT_STARTING_RATING),
            "away_pre_rating": current_ratings.get(away_id, DEFAULT_STARTING_RATING),
        }
    }


def _team_coordinates_for(storage, sport: str, *team_ids: str) -> dict[str, tuple[float, float]]:
    """{team_id: (latitude, longitude)} for exactly the team_ids given --
    build_event_features/build_player_features only ever look up the
    target event's own home/away pair (see library/features/ncaafb.py's
    geo.travel_distances_km call), unlike feature-engineering's
    build_dataset.py which needs every team encountered across the whole
    training set. Missing coordinates are simply omitted, same convention
    build_dataset.py's own _team_coordinates uses."""
    coordinates = {}
    for team_id in team_ids:
        entity = storage.get_entity(sport, team_id)
        if entity is None:
            continue
        metadata = entity.get("metadata", {})
        latitude, longitude = metadata.get("latitude"), metadata.get("longitude")
        if latitude is not None and longitude is not None:
            coordinates[team_id] = (latitude, longitude)
    return coordinates


def _team_previous_event_date(storage, sport: str, team_id: str, before_date: str, events: list[dict] | None = None) -> str | None:
    previous = storage.get_team_events(sport, team_id, before_date=before_date, limit=1, events=events)
    return previous[0]["event_date"] if previous else None


def _team_player_games_for_event(storage, team_id: str, event_key: str) -> list[dict]:
    return [row for row in storage.get_player_game_stats_for_event(event_key) if row.get("team_id") == team_id]


def _still_on_team(storage, sport: str, entity_id: str, team_id: str) -> bool:
    entity = storage.get_entity(sport, entity_id)
    return bool(entity) and (entity.get("metadata") or {}).get("team_id") == team_id


def _presumptive_leader(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, category: str, window: int,
    events: list[dict] | None = None,
) -> tuple[str, list[dict]] | None:
    """(entity_id, their own recent game history) for team_id's
    presumptive category leader (category in LEADER_IDENTIFIERS), or None
    if no still-rostered leader is found within the lookback bound -- see
    this module's own docstring for the full search/verification
    algorithm. `events` is this module's own already-fetched-history
    pass-through -- see this module's own docstring."""
    identify_fn = LEADER_IDENTIFIERS[category]
    team_events = storage.get_team_events(sport, team_id, before_date=before_date, events=events)
    for event in team_events:
        season = event.get("season")
        if season is not None and current_season is not None and season < current_season - SEASON_LOOKBACK:
            break  # exceeded the lookback bound -- events only get older from here
        candidate = identify_fn(_team_player_games_for_event(storage, team_id, event["event_key"]))
        if candidate is None:
            continue
        entity_id = candidate["entity_id"]
        if _still_on_team(storage, sport, entity_id, team_id):
            return entity_id, storage.get_player_game_stats(entity_id, before_date=before_date, limit=window)
    return None


def _build_player_feature_row(
    storage, sport: str, event: dict, home_id: str, away_id: str, entity_id: str, team_id: str,
    prior_games: list[dict], team_coordinates: dict, window: int, current_ratings: dict | None = None,
    events: list[dict] | None = None,
) -> dict:
    player_game = {
        "event_key": event["event_key"],
        "player_key": player_key(sport, entity_id),
        "entity_id": entity_id,
        "team_id": team_id,
        "event_date": event["event_date"],
        "stat_line": {},  # unknown -- this is what's being predicted, not labeled
    }
    return build_player_features(
        player_game, prior_games, event,
        _live_elo_ratings(storage, sport, event, home_id, away_id, current_ratings, events),
        _team_previous_event_date(storage, sport, team_id, event["event_date"], events),
        team_coordinates, window,
    )


def build_live_event_features(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """One event-level feature row for event_key, in the exact shape
    train_win_probability_model.py/train_score_model.py were trained on
    (label_* fields will be None/absent for an unplayed game -- callers
    only read the feature columns off the result, never the labels).

    `events` is this module's own already-fetched-history pass-through
    (see this module's own docstring) -- fetched once here if not given,
    and threaded through every one of this function's own 8 downstream
    get_team_events calls (2 rolling windows + 6 presumptive-leader
    searches) instead of each one separately re-querying. team_game_stats
    gets the identical one-fetch treatment locally, for its own 2 calls."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)
    before_date = event["event_date"]
    current_season = event.get("season")
    events = events if events is not None else storage.get_all_events(sport)
    team_game_stats = storage.get_all_team_game_stats(sport)

    home_events = storage.get_team_events(sport, home_id, before_date=before_date, limit=window, events=events)
    away_events = storage.get_team_events(sport, away_id, before_date=before_date, limit=window, events=events)
    home_box = storage.get_team_game_stats_for_team(sport, home_id, before_date=before_date, limit=window, team_game_stats=team_game_stats)
    away_box = storage.get_team_game_stats_for_team(sport, away_id, before_date=before_date, limit=window, team_game_stats=team_game_stats)
    team_coordinates = _team_coordinates_for(storage, sport, home_id, away_id)

    def leader_games(team_id: str, category: str) -> list[dict] | None:
        found = _presumptive_leader(storage, sport, team_id, before_date, current_season, category, window, events)
        return found[1] if found else None

    return build_event_features(
        event, _live_elo_ratings(storage, sport, event, home_id, away_id, events=events),
        home_events, away_events, team_coordinates, window,
        home_qb_games=leader_games(home_id, "passing"),
        away_qb_games=leader_games(away_id, "passing"),
        home_rb_games=leader_games(home_id, "rushing"),
        away_rb_games=leader_games(away_id, "rushing"),
        home_wr_games=leader_games(home_id, "receiving"),
        away_wr_games=leader_games(away_id, "receiving"),
        home_team_box_stats=home_box, away_team_box_stats=away_box,
    )


def build_live_player_features(
    storage, sport: str, event_key: str, entity_id: str, window: int = DEFAULT_ROLLING_WINDOW,
    current_ratings: dict | None = None, events: list[dict] | None = None,
) -> dict:
    """One player-prop feature row for entity_id's performance in
    event_key, in the exact shape train_player_prop_model.py was trained
    on. team_id comes from the player's own entity record
    (metadata.team_id, kept current by roster sync and box-score upserts),
    not from their own last game log, since a just-traded/transferred
    player's most recent game log would still show their old team.
    `events`/`current_ratings` are this module's own optional already-
    fetched pass-throughs -- see this module's own docstring."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    entity = storage.get_entity(sport, entity_id)
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = (entity.get("metadata") or {}).get("team_id")

    team_coordinates = _team_coordinates_for(storage, sport, home_id, away_id)
    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    return _build_player_feature_row(
        storage, sport, event, home_id, away_id, entity_id, team_id,
        prior_games, team_coordinates, window, current_ratings, events,
    )


def build_live_event_leaders(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """{"home": {"passing": row|None, "receiving": row|None, "rushing":
    row|None}, "away": {...}} -- ONE candidate per category per team
    (matching identify_starting_qb/identify_lead_rusher/identify_lead_
    receiver's own single-winner semantics, unlike NFL's ranked candidate
    pools), each already a full build_live_player_features-shaped row.
    None for a category with no still-rostered leader found within the
    lookback bound. See this module's own docstring for why there's no
    sacks key.

    `events` is this module's own already-fetched-history pass-through --
    event_prediction.predict_event fetches it once and passes the SAME
    list here and to build_live_event_features, so a single "predict one
    event" request pays for exactly one get_all_events call between the
    two of them, not one each."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)
    before_date = event["event_date"]
    current_season = event.get("season")
    team_coordinates = _team_coordinates_for(storage, sport, home_id, away_id)
    events = events if events is not None else storage.get_all_events(sport)
    _, current_ratings = compute_elo_ratings(events, as_of_season=current_season)

    def team_leaders(team_id: str) -> dict:
        result = {}
        for category in LEADER_IDENTIFIERS:
            found = _presumptive_leader(storage, sport, team_id, before_date, current_season, category, window, events)
            if found is None:
                result[category] = None
                continue
            entity_id, prior_games = found
            result[category] = _build_player_feature_row(
                storage, sport, event, home_id, away_id, entity_id, team_id,
                prior_games, team_coordinates, window, current_ratings, events,
            )
        return result

    return {"home": team_leaders(home_id), "away": team_leaders(away_id)}
