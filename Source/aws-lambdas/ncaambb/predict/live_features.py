"""
Assembles a live feature vector for one not-yet-played NCAA MBB event or
player-prop target, using the same pure functions (build_event_features/
build_player_features, library/features/ncaambb.py) that build the
training datasets. Byte-for-byte the same shape as nba/predict/
live_features.py -- see that module's own docstring for the reasoning
this one inherits unchanged.

No team_coordinates/travel_km parameter -- library.features.ncaambb has
none at all (see that module's own docstring: no geo-coordinate data
source exists for NCAA MBB). No presumptive-starter tracking -- there's
no position whose own performance dominates a team's outcome in
basketball, so build_event_features carries no argument to populate here.

Leaders panel (build_live_event_leader_candidates) needs its own
per-category candidate search: no roster/depth-chart position data to
pick a starter from, so candidates are every still-rostered player
credited with the category's volume stat anywhere in the team's recent
box scores, ranked by recent volume. Basketball's own category set is
scoring/rebounding/assists (see library.serving.ncaambb_reads' own
_STAT_CATEGORY). "Still-rostered" (_still_on_team) means both matches
team_id AND was recently reconfirmed there (_is_roster_entry_fresh) --
matching team_id alone isn't enough, since a player who's simply
stopped appearing anywhere (box scores or the daily roster re-fetch --
graduated, quit, transferred out) never gets an explicit removal write;
nothing ever un-sets their last-known team_id.

`events` (an already-fetched get_all_events(sport) result) is an optional
pass-through accepted by every public function here and threaded into
get_team_events/_live_elo_ratings calls, so one event-prediction request
can share a single fetch instead of each function re-querying.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from library.features.common import compute_elo_ratings
from library.features.live_orchestration import EventNotFoundError, MalformedEventError
from library.features.live_orchestration import home_away_ids as _home_away_ids
from library.features.live_orchestration import is_roster_entry_fresh
from library.features.live_orchestration import live_elo_ratings as _live_elo_ratings
from library.features.live_orchestration import recent_volume as _recent_volume
from library.features.live_orchestration import team_player_games_for_event as _team_player_games_for_event
from library.features.live_orchestration import team_previous_event_date as _team_previous_event_date
from library.features.live_orchestration import top_n_by_recent_volume as _top_n_by_recent_volume
from library.features.ncaambb import build_event_features, build_player_features
from library.schema.keys import player_key

DEFAULT_ROLLING_WINDOW = 5
SEASON_LOOKBACK = 1

# A player entity's metadata.team_id_as_of older than this many days is
# treated as "not recently confirmed on this team" rather than trusted
# indefinitely -- see _is_roster_entry_fresh. NCAA MBB's own ingest
# re-fetches every team's full roster on every daily run (ingest/
# handler.py's own _fetch_rosters -- "always fresh, never TTL-cached"),
# so this can stay tight, generous only enough to absorb a handful of
# missed runs -- matches nfl/predict/live_features.py's own
# _ROSTER_STALENESS_DAYS, which is tight for the same reason.
_ROSTER_STALENESS_DAYS = 14

# get_team_game_stats_for_team only keeps each team's DEFAULT_ROLLING_WINDOW
# most recent games -- bounding the underlying get_all_team_game_stats
# Query to this many days back (instead of every row ever written for the
# sport) keeps it a recent-window Query, not a full-season partition read.
# Generous relative to NCAA MBB's own ~2-3 games/week pace, including
# through a holiday break.
TEAM_GAME_STATS_LOOKBACK_DAYS = 60

# Leaders-panel candidate generation -- box-score volume stat per category.
# Matches library.serving.ncaambb_reads' own _CATEGORY_PRIMARY_STAT.
LEADER_VOLUME_STATS = {"scoring": "points", "rebounding": "rebounds", "assists": "assists"}
LEADER_CANDIDATE_LIMITS = {"scoring": 5, "rebounding": 5, "assists": 5}


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
    since_date = (date.fromisoformat(before_date) - timedelta(days=TEAM_GAME_STATS_LOOKBACK_DAYS)).isoformat()
    team_game_stats = storage.get_all_team_game_stats(sport, since_date=since_date)

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

    entity = storage.get_entity(sport, entity_id, "player")
    if entity is None:
        raise EventNotFoundError(f"No entity found for {entity_id}")
    team_id = (entity.get("metadata") or {}).get("team_id")

    prior_games = storage.get_player_game_stats(entity_id, before_date=event["event_date"], limit=window)
    return _build_player_feature_row(
        storage, sport, event, home_id, away_id, entity_id, team_id, prior_games, window, current_ratings, events,
    )


def _box_score_candidate_ids(
    storage, sport: str, team_id: str, before_date: str, current_season: int | None, stat_key: str,
    events: list[dict] | None = None, reference_date: str | None = None,
) -> list[str]:
    """Every still-rostered entity_id credited with stat_key at least once
    in team_id's box score history, walking most-recent-first, bounded to
    the current season plus SEASON_LOOKBACK prior. Two phases: first
    collect every distinct candidate id (cheap, no I/O beyond the
    already-fetched events/box-score rows), then check roster membership
    concurrently -- sequential roster checks are a real latency source
    once a team's SEASON_LOOKBACK-bounded history holds dozens of
    distinct candidates for a single category. reference_date is today by
    default (see _still_on_team) -- threaded through so every candidate
    in one request is judged against the same moment, and so tests can
    pin it."""
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
