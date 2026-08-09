"""
NCAAFB feature engineering -- the CFBD-sourced equivalent of
Source/feature-engineering/nfl/build_dataset.py. Pulls full history from
DynamoDB (via FeatureStorage), computes event-level, player-level, and
team-week-level (National Ranking model) training features using
library.features.ncaafb's pure functions, and writes three Parquet
training datasets to S3 (via S3Manager) for the training Fargate tasks to
read.

Not scheduled -- run manually via `aws ecs run-task`, same as
Source/data-backfills/ncaafb (see
Terraform/ecs-task-ncaafb-feature-engineering.tf). Safe to re-run at any
time: it always rebuilds all three datasets from the current DynamoDB
contents and overwrites the same three S3 keys.

Unlike NFL, there's no exhibition-game contamination filter (no Pro Bowl
equivalent in college football) -- every completed event with a resolvable
home/away role is used as-is.

Required environment variables:
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- games of history each event/player
    rolling average covers, see library.features.common.DEFAULT_ROLLING_
    WINDOW. Does not affect the ranking dataset, which always uses each
    team's full season-to-date history (see build_team_week_features).

Usage:
    python build_dataset.py
"""
import io
import json
import logging
import os
from collections import defaultdict

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.features.common import compute_elo_ratings
from library.features.ncaafb import (
    build_event_features,
    build_player_features,
    build_team_week_features,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
)
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-feature-engineering")

SPORT = "ncaafb"
EVENT_FEATURES_KEY = "ncaafb/training-data/event_features.parquet"
PLAYER_FEATURES_KEY = "ncaafb/training-data/player_features.parquet"
RANKING_FEATURES_KEY = "ncaafb/training-data/ranking_features.parquet"


def _team_ids(events: list[dict]) -> set[str]:
    ids = set()
    for event in events:
        for participant in event.get("participants", []):
            entity_id = participant.get("entity_id")
            if entity_id is not None:
                ids.add(entity_id)
    return ids


def _team_coordinates(storage: FeatureStorage, team_ids: set[str]) -> dict[str, tuple[float, float]]:
    """{entity_id: (latitude, longitude)} from each team entity's own
    metadata (see library/normalize/ncaafb.py's team_to_entity) -- one
    GetItem per team encountered (~136 for FBS, trivial), not a hardcoded
    module constant the way NFL's nfl_teams.TEAM_COORDINATES is. Missing
    or incomplete coordinates for a team are simply omitted -- geo.
    travel_distances_km already treats an unresolvable team_id as None,
    not a crash."""
    coordinates = {}
    for team_id in team_ids:
        entity = storage.get_entity(SPORT, team_id)
        if entity is None:
            continue
        metadata = entity.get("metadata", {})
        latitude, longitude = metadata.get("latitude"), metadata.get("longitude")
        if latitude is not None and longitude is not None:
            coordinates[team_id] = (latitude, longitude)
    return coordinates


def _group_player_games_by_event_and_team(player_games: list[dict]) -> dict[tuple[str, str], list[dict]]:
    by_event_team: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for game in player_games:
        by_event_team[(game["event_key"], game["team_id"])].append(game)
    return by_event_team


def _index_team_game_stats(team_game_stats: list[dict]) -> dict[tuple[str, str], dict]:
    return {(row["event_key"], row["team_id"]): row for row in team_game_stats}


def _leader_and_history(
    player_games_by_event_team: dict[tuple[str, str], list[dict]],
    history: dict[str, list[dict]],
    identify_fn,
    event_key: str,
    team_id: str,
    window: int,
) -> tuple[dict | None, list[dict]]:
    game = identify_fn(player_games_by_event_team.get((event_key, team_id), []))
    prior_games = history[game["entity_id"]][-window:][::-1] if game else []
    return game, prior_games


def build_event_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    """Same incremental chronological walk as NFL's build_event_dataset --
    see that function's own docstring for the O(games) reasoning."""
    events = storage.get_all_events(SPORT)
    logger.info("Loaded %d completed events", len(events))

    team_coordinates = _team_coordinates(storage, _team_ids(events))
    player_games_by_event_team = _group_player_games_by_event_and_team(storage.get_all_player_game_stats(SPORT))
    team_game_stats_by_event_team = _index_team_game_stats(storage.get_all_team_game_stats(SPORT))

    elo_ratings, _ = compute_elo_ratings(events)
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))

    team_history: dict[str, list[dict]] = defaultdict(list)
    qb_history: dict[str, list[dict]] = defaultdict(list)
    rb_history: dict[str, list[dict]] = defaultdict(list)
    wr_history: dict[str, list[dict]] = defaultdict(list)
    team_box_history: dict[str, list[dict]] = defaultdict(list)
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        participants = event.get("participants", [])
        home = next((p for p in participants if p.get("role") == "home"), None)
        away = next((p for p in participants if p.get("role") == "away"), None)
        if home is None or away is None:
            logger.debug("Skipping event %s -- missing home/away role", event.get("event_key"))
            continue

        home_id, away_id = home["entity_id"], away["entity_id"]
        home_history = team_history[home_id][-window:][::-1]
        away_history = team_history[away_id][-window:][::-1]
        home_box_history = team_box_history[home_id][-window:][::-1]
        away_box_history = team_box_history[away_id][-window:][::-1]

        event_key = event["event_key"]
        home_qb_game, home_qb_history = _leader_and_history(
            player_games_by_event_team, qb_history, identify_starting_qb, event_key, home_id, window)
        away_qb_game, away_qb_history = _leader_and_history(
            player_games_by_event_team, qb_history, identify_starting_qb, event_key, away_id, window)
        home_rb_game, home_rb_history = _leader_and_history(
            player_games_by_event_team, rb_history, identify_lead_rusher, event_key, home_id, window)
        away_rb_game, away_rb_history = _leader_and_history(
            player_games_by_event_team, rb_history, identify_lead_rusher, event_key, away_id, window)
        home_wr_game, home_wr_history = _leader_and_history(
            player_games_by_event_team, wr_history, identify_lead_receiver, event_key, home_id, window)
        away_wr_game, away_wr_history = _leader_and_history(
            player_games_by_event_team, wr_history, identify_lead_receiver, event_key, away_id, window)

        rows.append(build_event_features(
            event, elo_ratings, home_history, away_history, team_coordinates, window,
            home_qb_games=home_qb_history, away_qb_games=away_qb_history,
            home_rb_games=home_rb_history, away_rb_games=away_rb_history,
            home_wr_games=home_wr_history, away_wr_games=away_wr_history,
            home_team_box_stats=home_box_history, away_team_box_stats=away_box_history,
        ))

        team_history[home_id].append(event)
        team_history[away_id].append(event)
        for game, history in (
            (home_qb_game, qb_history), (away_qb_game, qb_history),
            (home_rb_game, rb_history), (away_rb_game, rb_history),
            (home_wr_game, wr_history), (away_wr_game, wr_history),
        ):
            if game:
                history[game["entity_id"]].append(game)

        home_box_row = team_game_stats_by_event_team.get((event_key, home_id))
        away_box_row = team_game_stats_by_event_team.get((event_key, away_id))
        if home_box_row:
            team_box_history[home_id].append(home_box_row)
        if away_box_row:
            team_box_history[away_id].append(away_box_row)

        if i % 500 == 0 or i == total:
            logger.info("Built event features: %d/%d", i, total)

    return rows


def _group_player_games_by_player(player_games: list[dict]) -> dict[str, list[dict]]:
    by_player: dict[str, list[dict]] = defaultdict(list)
    for game in player_games:
        by_player[game["entity_id"]].append(game)
    for games in by_player.values():
        games.sort(key=lambda g: g.get("event_date", ""))
    return by_player


def _team_previous_event_dates(events: list[dict]) -> dict[tuple[str, str], str | None]:
    by_team: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        for participant in event.get("participants", []):
            by_team[participant["entity_id"]].append(event)

    previous_dates: dict[tuple[str, str], str | None] = {}
    for team_id, team_events in by_team.items():
        team_events.sort(key=lambda e: e.get("event_date", ""))
        previous_date = None
        for event in team_events:
            previous_dates[(team_id, event["event_key"])] = previous_date
            previous_date = event.get("event_date")
    return previous_dates


def build_player_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    """Same incremental per-player walk as NFL's build_player_dataset."""
    events = storage.get_all_events(SPORT)
    events_by_key = {event["event_key"]: event for event in events}
    team_coordinates = _team_coordinates(storage, _team_ids(events))
    elo_ratings, _ = compute_elo_ratings(events)
    team_previous_event_dates = _team_previous_event_dates(events)

    player_games = storage.get_all_player_game_stats(SPORT)
    logger.info("Loaded %d player-game rows", len(player_games))

    games_by_player = _group_player_games_by_player(player_games)

    total = len(player_games)
    seen = 0
    skipped = 0
    rows = []
    for games in games_by_player.values():
        history: list[dict] = []
        for game in games:
            event = events_by_key.get(game["event_key"])
            participants = event.get("participants", []) if event else []
            has_home_and_away = any(p.get("role") == "home" for p in participants) and any(
                p.get("role") == "away" for p in participants
            )
            if not has_home_and_away:
                logger.debug("Skipping player-game %s -- event missing or missing home/away role", game["event_key"])
                skipped += 1
            else:
                prior = history[-window:][::-1]
                own_previous_event_date = team_previous_event_dates.get((game["team_id"], game["event_key"]))
                rows.append(build_player_features(game, prior, event, elo_ratings, own_previous_event_date, team_coordinates, window))
                history.append(game)

            seen += 1
            if seen % 20000 == 0 or seen == total:
                logger.info("Built player features: %d/%d (%d skipped)", seen, total, skipped)

    return rows


def build_ranking_dataset(storage: FeatureStorage) -> list[dict]:
    """Team-week granularity, for the National Ranking model -- distinct
    from build_event_dataset/build_player_dataset's per-game grain (see
    library.features.ncaafb.build_team_week_features' own docstring).
    Each team's own season history grows unboundedly here (unlike the
    window-capped event/player walks above) since the ranking model wants
    season-to-date record/scoring/SOS, not a trailing N-game average --
    bounded in practice by one FBS season's ~13-17 games per team.
    """
    events = storage.get_all_events(SPORT)
    elo_ratings, _ = compute_elo_ratings(events)
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))

    team_season_history: dict[tuple[str, int], list[dict]] = defaultdict(list)
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        participants = event.get("participants", [])
        home = next((p for p in participants if p.get("role") == "home"), None)
        away = next((p for p in participants if p.get("role") == "away"), None)
        if home is None or away is None:
            continue

        season = event.get("season")
        for team_id in (home["entity_id"], away["entity_id"]):
            key = (team_id, season)
            team_history = team_season_history[key][::-1]
            rows.append(build_team_week_features(team_id, event, elo_ratings, team_history))
            team_season_history[key].append(event)

        if i % 500 == 0 or i == total:
            logger.info("Built ranking features: %d/%d events", i, total)

    return rows


def _write_parquet(rows: list[dict]) -> bytes:
    """Same union-of-columns, JSON-encode-dict-values approach as NFL's
    own _write_parquet -- see its docstring."""
    if not rows:
        return b""
    df = pd.DataFrame(rows)
    dict_columns = [key for key, value in rows[0].items() if isinstance(value, dict)]
    for col in dict_columns:
        df[col] = df[col].apply(json.dumps)
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def _write_dataset(s3: S3Manager, key: str, rows: list[dict], label: str) -> None:
    if not rows:
        raise RuntimeError(f"{label} produced 0 rows -- refusing to overwrite s3://{s3.bucket}/{key} with an empty dataset")
    logger.info("Writing %d %s rows to Parquet...", len(rows), label)
    s3.put_bytes(key, _write_parquet(rows), content_type="application/octet-stream")
    logger.info("Wrote %d %s rows to s3://%s/%s", len(rows), label, s3.bucket, key)


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")

    logger.info("Starting NCAAFB feature engineering (rolling window=%d games)", window)

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)

    logger.info("Building event-level dataset...")
    _write_dataset(s3, EVENT_FEATURES_KEY, build_event_dataset(storage, window), "event")

    logger.info("Building player-level dataset...")
    player_rows = build_player_dataset(storage, window)
    player_row_count = len(player_rows)
    if not player_row_count:
        raise RuntimeError(f"build_player_dataset produced 0 rows -- refusing to overwrite s3://{bucket}/{PLAYER_FEATURES_KEY}")
    logger.info("Writing %d player feature rows to Parquet...", player_row_count)
    player_parquet = _write_parquet(player_rows)
    del player_rows  # free the largest of the three row lists before its own upload, not just after
    s3.put_bytes(PLAYER_FEATURES_KEY, player_parquet, content_type="application/octet-stream")
    logger.info("Wrote %d player feature rows to s3://%s/%s", player_row_count, bucket, PLAYER_FEATURES_KEY)

    logger.info("Building team-week ranking dataset...")
    _write_dataset(s3, RANKING_FEATURES_KEY, build_ranking_dataset(storage), "ranking")

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    main()
