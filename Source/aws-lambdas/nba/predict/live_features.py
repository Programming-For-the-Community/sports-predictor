"""
Assembles a live feature vector for one not-yet-played NBA event or
player-prop target, using the same pure functions (build_event_features/
build_player_features, library/features/nba.py) that build the training
datasets.

Simpler than NCAAFB's/NFL's own live_features.py in two ways, both
mirroring library.features.nba.build_event_features'/build_player_features'
own docstring:
- No team_coordinates parameter -- library.features.nba_teams is a static
  30-franchise table, called directly rather than threaded through from a
  live entity lookup the way NCAAFB's dynamic per-season coordinates are.
- No presumptive-starter tracking (no NCAAFB-style identify_starting_qb
  equivalent) -- there's no position whose own performance dominates a
  team's outcome in basketball, so build_event_features carries no
  home_qb_games-style argument to populate here.

Leaders panel (build_live_event_leader_candidates) still needs its own
per-category candidate search, same reasoning NCAAFB's own live_features.py
gives: no roster/depth-chart position data to pick a starter from, so
candidates are every still-rostered player credited with the category's
volume stat anywhere in the team's recent box scores, ranked by recent
volume. Basketball's own category set is scoring/rebounding/assists (see
library.serving.nba_reads' own _STAT_CATEGORY), not football's.

`events` (an already-fetched get_all_events(sport) result) is an optional
pass-through accepted by every public function here and threaded into
get_team_events/_live_elo_ratings calls, so one event-prediction request
can share a single fetch instead of each function re-querying.
"""
from concurrent.futures import ThreadPoolExecutor

from library.features.common import DEFAULT_STARTING_RATING, compute_elo_ratings
from library.features.nba import build_event_features, build_player_features
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5
SEASON_LOOKBACK = 1

# Leaders-panel candidate generation -- box-score volume stat per category.
# Matches library.serving.nba_reads' own _CATEGORY_PRIMARY_STAT.
LEADER_VOLUME_STATS = {"scoring": "points", "rebounding": "rebounds", "assists": "assists"}
LEADER_CANDIDATE_LIMITS = {"scoring": 2, "rebounding": 2, "assists": 2}


class EventNotFoundError(Exception):
    pass


class MalformedEventError(Exception):
    """The event exists but is missing a home/away participant role."""


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
    if current_ratings is None:
        completed_events = events if events is not None else storage.get_all_events(sport)
        _, current_ratings = compute_elo_ratings(completed_events, as_of_season=event.get("season"))
    return {
        event["event_key"]: {
            "home_pre_rating": current_ratings.get(home_id, DEFAULT_STARTING_RATING),
            "away_pre_rating": current_ratings.get(away_id, DEFAULT_STARTING_RATING),
        }
    }


def _team_previous_event_date(storage, sport: str, team_id: str, before_date: str, events: list[dict] | None = None) -> str | None:
    previous = storage.get_team_events(sport, team_id, before_date=before_date, limit=1, events=events)
    return previous[0]["event_date"] if previous else None


def _team_player_games_for_event(storage, team_id: str, event_key: str) -> list[dict]:
    return [row for row in storage.get_player_game_stats_for_event(event_key) if row.get("team_id") == team_id]


def _still_on_team(storage, sport: str, entity_id: str, team_id: str) -> bool:
    entity = storage.get_entity(sport, entity_id)
    return bool(entity) and (entity.get("metadata") or {}).get("team_id") == team_id


def _build_player_feature_row(
    storage, sport: str, event: dict, home_id: str, away_id: str, entity_id: str, team_id: str,
    prior_games: list[dict], window: int, current_ratings: dict | None = None, events: list[dict] | None = None,
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
        window,
    )


def build_live_event_features(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """One event-level feature row for event_key, in the shape
    train_win_probability_model.py/train_score_model.py were trained on."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)
    before_date = event["event_date"]
    events = events if events is not None else storage.get_all_events(sport)
    team_game_stats = storage.get_all_team_game_stats(sport)

    home_events = storage.get_team_events(sport, home_id, before_date=before_date, limit=window, events=events)
    away_events = storage.get_team_events(sport, away_id, before_date=before_date, limit=window, events=events)
    home_box = storage.get_team_game_stats_for_team(sport, home_id, before_date=before_date, limit=window, team_game_stats=team_game_stats)
    away_box = storage.get_team_game_stats_for_team(sport, away_id, before_date=before_date, limit=window, team_game_stats=team_game_stats)

    return build_event_features(
        event, _live_elo_ratings(storage, sport, event, home_id, away_id, events=events),
        home_events, away_events, window,
        home_team_box_stats=home_box, away_team_box_stats=away_box,
    )


def build_live_player_features(
    storage, sport: str, event_key: str, entity_id: str, window: int = DEFAULT_ROLLING_WINDOW,
    current_ratings: dict | None = None, events: list[dict] | None = None,
) -> dict:
    """One player-prop feature row for entity_id's performance in event_key.
    team_id comes from the player's own entity record, not their last game
    log."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)

    entity = storage.get_entity(sport, entity_id)
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = (entity.get("metadata") or {}).get("team_id")

    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    return _build_player_feature_row(
        storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window, current_ratings, events,
    )


def _box_score_candidate_ids(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, stat_key: str,
    events: list[dict] | None = None,
) -> list[str]:
    """Every still-rostered entity_id credited with stat_key at least once
    in team_id's box score history, walking most-recent-first, bounded to
    the current season plus SEASON_LOOKBACK prior. Two phases: first
    collect every distinct candidate id (cheap, no I/O beyond the
    already-fetched events/box-score rows), then check roster membership
    concurrently -- same reasoning ncaafb's own _box_score_candidate_ids
    gives for why sequential roster checks are a real, reported latency
    source once a team's SEASON_LOOKBACK-bounded history holds dozens of
    distinct candidates for a single category."""
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
            executor.map(lambda entity_id: _still_on_team(storage, sport, entity_id, team_id), ordered_ids),
        ))
    return [entity_id for entity_id in ordered_ids if still_rostered[entity_id]]


def _recent_volume(storage, entity_id: str, stat: str, before_date: str, window: int) -> tuple[float, list[dict]]:
    games = storage.get_player_game_stats(entity_id, before_date=before_date, limit=window)
    total = sum(game.get("stat_line", {}).get(stat, 0) for game in games)
    return total, games


def _top_n_by_recent_volume(
    storage, entity_ids: list[str], stat: str, before_date: str, window: int, n: int,
) -> dict[str, list[dict]]:
    """entity_id -> recent game history, for the top n of entity_ids by recent volume of stat."""
    if not entity_ids:
        return {}
    volumes = {entity_id: _recent_volume(storage, entity_id, stat, before_date, window) for entity_id in entity_ids}
    ranked = sorted(volumes, key=lambda entity_id: volumes[entity_id][0], reverse=True)[:n]
    return {entity_id: volumes[entity_id][1] for entity_id in ranked}


def build_live_event_leader_candidates(
    storage, sport: str, event_key: str, window: int = DEFAULT_ROLLING_WINDOW,
    events: list[dict] | None = None,
) -> dict:
    """One feature row per candidate likely to lead each team in
    scoring/rebounding/assists for event_key, grouped {"home": {"scoring":
    [row, row], "rebounding": [row, row], "assists": [row, row]}, "away":
    {...}} -- each row is build_player_features' usual output (carries
    entity_id). Deliberately doesn't touch S3 or load any model -- scoring
    these against the right player-prop model is the caller's job
    (event_prediction.py), same separation build_live_player_features/
    model_loader.py have."""
    event = storage.get_event(event_key)
    if event is None:
        raise EventNotFoundError(f"No event found for {event_key}")
    home_id, away_id = _home_away_ids(event)
    before_date = event["event_date"]
    current_season = event.get("season")
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
                    storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window, current_ratings, events,
                )
                for entity_id, prior_games in histories.items()
            ]
        return result

    return {"home": team_candidates(home_id), "away": team_candidates(away_id)}
