"""
Assembles a live feature vector for one not-yet-played NCAAFB event or
player-prop target, using the same pure functions (build_event_features/
build_player_features, library/features/ncaafb.py) that build the
training datasets. NCAAFB has no depth-chart/injury data, so leaders are
identified from box-score history instead of a roster/depth chart.

Presumptive QB/RB/WR leader (for build_live_event_features' rolling
QB/RB/WR history): walks a team's completed events (most recent first)
for a past game whose box score identifies a starter
(library.features.ncaafb.identify_starting_qb/identify_lead_rusher/
identify_lead_receiver), bounded to the current season plus
SEASON_LOOKBACK prior seasons. The candidate is cross-checked against
their entity record's current team_id so a transfer isn't attributed to
their old team -- see _still_on_team/_is_roster_entry_fresh. Residual
gap: a player who left the program for a new team that hasn't yet
played (or re-fetched its roster) since the transfer still shows their
old team_id until that catches up.

Leaders panel (build_live_event_leader_candidates): a separate, multi-
candidate version of the same idea -- no depth chart/roster position
data exists for NCAAFB, so candidates are every still-rostered player
credited with the category's volume stat anywhere in the team's recent
box scores, ranked by recent volume. "Still-rostered" (_still_on_team)
means both matches team_id AND was recently reconfirmed there --
matching team_id alone isn't enough, since a player who's simply
stopped appearing anywhere (box scores or roster sync -- graduated,
quit, went pro) never gets an explicit removal write; nothing ever
un-sets their last-known team_id. See _is_roster_entry_fresh.

`events` (an already-fetched get_all_events(sport) result) is an
optional pass-through accepted by every public function here and threaded
into get_team_events/_live_elo_ratings calls, so one event-prediction
request can share a single fetch instead of each function re-querying.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from library.features.common import compute_elo_ratings
from library.features.live_orchestration import EventNotFoundError, MalformedEventError
from library.features.live_orchestration import home_away_ids as _home_away_ids
from library.features.live_orchestration import is_roster_entry_fresh
from library.features.live_orchestration import live_elo_ratings as _live_elo_ratings
from library.features.live_orchestration import recent_volume as _recent_volume
from library.features.live_orchestration import team_player_games_for_event as _team_player_games_for_event
from library.features.live_orchestration import team_previous_event_date as _team_previous_event_date
from library.features.live_orchestration import top_n_by_recent_volume as _top_n_by_recent_volume
from library.features.ncaafb import (
    build_event_features,
    build_player_features,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
)
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5
SEASON_LOOKBACK = 1

# A player entity's metadata.team_id_as_of older than this many days is
# treated as "not recently confirmed on this team" rather than trusted
# indefinitely -- see _is_roster_entry_fresh. CFBD's own bulk roster is
# only re-fetched every ROSTER_CACHE_TTL_DAYS=30 (ingest/handler.py), so
# this needs real slack above that for a player who simply hasn't played
# since the last refresh; nfl/predict/live_features.py's own
# _ROSTER_STALENESS_DAYS=14 is tighter only because NFL's roster/injury
# feed refreshes far more often.
_ROSTER_STALENESS_DAYS = 45

LEADER_IDENTIFIERS = {
    "passing": identify_starting_qb,
    "rushing": identify_lead_rusher,
    "receiving": identify_lead_receiver,
}

# Leaders-panel candidate generation -- box-score volume stat and final
# candidate count per category.
LEADER_VOLUME_STATS = {
    "passing": "passing_attempts",
    "rushing": "rushing_attempts",
    "receiving": "receiving_receptions",
    "sacks": "defensive_sacks",
}
LEADER_CANDIDATE_LIMITS = {"passing": 1, "rushing": 2, "receiving": 3, "sacks": 3}


def _team_coordinates_for(storage, sport: str, *team_ids: str) -> dict[str, tuple[float, float]]:
    coordinates = {}
    for team_id in team_ids:
        entity = storage.get_entity(sport, team_id, "team")
        if entity is None:
            continue
        metadata = entity.get("metadata", {})
        latitude, longitude = metadata.get("latitude"), metadata.get("longitude")
        if latitude is not None and longitude is not None:
            coordinates[team_id] = (latitude, longitude)
    return coordinates


def _is_roster_entry_fresh(entity: dict, reference_date: str) -> bool:
    return is_roster_entry_fresh(entity, reference_date, _ROSTER_STALENESS_DAYS)


def _still_on_team(storage, sport: str, entity_id: str, team_id: str, reference_date: str | None = None) -> bool:
    """True only if entity_id's own team_id metadata both matches team_id
    AND was reconfirmed there recently (_is_roster_entry_fresh) -- a
    team_id match alone isn't enough, since nothing ever explicitly
    un-sets a player's last-known team_id once they stop appearing
    anywhere (see this module's own docstring)."""
    entity = storage.get_entity(sport, entity_id, "player")
    if not entity or (entity.get("metadata") or {}).get("team_id") != team_id:
        return False
    return _is_roster_entry_fresh(entity, reference_date or date.today().isoformat())


def _presumptive_leader(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, category: str, window: int,
    events: list[dict] | None = None, reference_date: str | None = None,
) -> tuple[str, list[dict]] | None:
    """(entity_id, their own recent game history) for team_id's presumptive category leader,
    or None if no still-rostered leader is found within the lookback bound.

    reference_date is today by default (see _still_on_team) -- passed
    through explicitly rather than resolved inside the loop so a caller
    computing several leaders for the same request shares one moment in
    time, and so tests can pin it instead of depending on real
    wall-clock time."""
    identify_fn = LEADER_IDENTIFIERS[category]
    team_events = storage.get_team_events(sport, team_id, before_date=before_date, events=events)
    for event in team_events:
        season = event.get("season")
        if season is not None and current_season is not None and season < current_season - SEASON_LOOKBACK:
            break
        candidate = identify_fn(_team_player_games_for_event(storage, team_id, event["event_key"]))
        if candidate is None:
            continue
        entity_id = candidate["entity_id"]
        if _still_on_team(storage, sport, entity_id, team_id, reference_date):
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
        "stat_line": {},
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
    """One event-level feature row for event_key, in the shape train_win_probability_model.py/
    train_score_model.py were trained on."""
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
    """One player-prop feature row for entity_id's performance in event_key. team_id comes
    from the player's own entity record, not their last game log."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    entity = storage.get_entity(sport, entity_id, "player")
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = (entity.get("metadata") or {}).get("team_id")

    team_coordinates = _team_coordinates_for(storage, sport, home_id, away_id)
    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    return _build_player_feature_row(
        storage, sport, event, home_id, away_id, entity_id, team_id,
        prior_games, team_coordinates, window, current_ratings, events,
    )


def _box_score_candidate_ids(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, stat_key: str,
    events: list[dict] | None = None, reference_date: str | None = None,
) -> list[str]:
    """Every still-rostered entity_id credited with stat_key at least once in team_id's box
    score history, walking most-recent-first, bounded the same as _presumptive_leader
    (current season plus SEASON_LOOKBACK prior).

    Two phases: first collect every distinct candidate id from box scores,
    then check roster membership (_still_on_team, one GetItem each)
    concurrently, since a team's SEASON_LOOKBACK-bounded box-score history
    can hold dozens of distinct candidates for a single category.
    reference_date is today by default (see _still_on_team) -- threaded
    through so every candidate in one request is judged against the same
    moment, and so tests can pin it."""
    team_events = storage.get_team_events(sport, team_id, before_date=before_date, events=events)
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for event in team_events:
        season = event.get("season")
        if season is not None and current_season is not None and season < current_season - SEASON_LOOKBACK:
            break
        for row in _team_player_games_for_event(storage, team_id, event["event_key"]):
            entity_id = row.get("entity_id")
            if entity_id is None or entity_id in seen or stat_key not in row.get("stat_line", {}):
                continue
            seen.add(entity_id)
            ordered_ids.append(entity_id)

    if not ordered_ids:
        return []
    with ThreadPoolExecutor(max_workers=min(len(ordered_ids), 16)) as executor:
        still_rostered = dict(zip(
            ordered_ids,
            executor.map(lambda entity_id: _still_on_team(storage, sport, entity_id, team_id, reference_date), ordered_ids),
        ))
    return [entity_id for entity_id in ordered_ids if still_rostered[entity_id]]


def build_live_event_leader_candidates(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """One feature row per candidate likely to lead each team in passing/rushing/receiving/
    sacks for event_key, grouped {"home": {"passing": [row], "rushing": [row, row],
    "receiving": [row, row, row], "sacks": [row, row, row]}, "away": {...}} -- each row is
    build_player_features' usual output (carries entity_id). Deliberately doesn't touch S3 or
    load any model -- scoring these against the right player-prop model is the caller's job
    (event_prediction.py)."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)
    before_date = event["event_date"]
    current_season = event.get("season")
    team_coordinates = _team_coordinates_for(storage, sport, home_id, away_id)
    events = events if events is not None else storage.get_all_events(sport)
    _, current_ratings = compute_elo_ratings(events, as_of_season=current_season)

    def team_candidates(team_id: str) -> dict:
        result = {}
        for category, stat_key in LEADER_VOLUME_STATS.items():
            candidate_ids = _box_score_candidate_ids(storage, sport, team_id, before_date, current_season, stat_key, events)
            histories = _top_n_by_recent_volume(
                storage, candidate_ids, stat_key, before_date, window, LEADER_CANDIDATE_LIMITS[category],
            )
            result[category] = [
                _build_player_feature_row(
                    storage, sport, event, home_id, away_id, entity_id, team_id,
                    prior_games, team_coordinates, window, current_ratings, events,
                )
                for entity_id, prior_games in histories.items()
            ]
        return result

    return {"home": team_candidates(home_id), "away": team_candidates(away_id)}
