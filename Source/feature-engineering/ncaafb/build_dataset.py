"""
NCAAFB feature engineering. Pulls full history from DynamoDB (via
FeatureStorage), computes event-level, player-level, and team-week-level
(National Ranking model) training features using library.features.ncaafb's
pure functions, and writes three Parquet training datasets to S3 (via
S3Manager) for the training Fargate tasks to read.

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds all three datasets from the current DynamoDB
contents and overwrites the same three S3 keys.

Every completed event with a resolvable home/away role is used as-is.

Required environment variables:
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- games of history each event/player
    rolling average covers. Does not affect the ranking dataset, which
    always uses each team's full season-to-date history.
    TRAINING_LOOKBACK_SEASONS -- caps how far back FeatureStorage reads,
    via a since_date computed as roughly that many seasons before today.
    Unset (the default) reads full history, same as before this existed.
    Applies to the ranking dataset too, despite that dataset's own
    "full season-to-date" note above -- that note is about not
    trailing-windowing within a season, not about how many seasons back
    to start from.

Usage:
    python build_dataset.py
"""
import logging
import os
from collections import defaultdict

from library.aws import xray
from library.aws.s3_manager import S3Manager
from library.features import build_dataset_common
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
    metadata -- one GetItem per team encountered. Missing or incomplete
    coordinates for a team are simply omitted."""
    coordinates = {}
    for team_id in team_ids:
        entity = storage.get_entity(SPORT, team_id, "team")
        if entity is None:
            continue
        metadata = entity.get("metadata", {})
        latitude, longitude = metadata.get("latitude"), metadata.get("longitude")
        if latitude is not None and longitude is not None:
            coordinates[team_id] = (latitude, longitude)
    return coordinates


LEADERS = {"qb": identify_starting_qb, "rb": identify_lead_rusher, "wr": identify_lead_receiver}


def build_event_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Every event's feature row, each team's history and each team's
    starting QB, lead rusher and lead receiver's history capped to the
    last `window` games."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    logger.info("Loaded %d completed events", len(events))
    team_coordinates = _team_coordinates(storage, _team_ids(events))

    def event_features(event, elo_ratings, home_history, away_history, window, **kwargs):
        return build_event_features(event, elo_ratings, home_history, away_history, team_coordinates, window, **kwargs)

    return build_dataset_common.build_team_event_rows(
        events, storage.get_all_team_game_stats(SPORT, since_date=since_date), window, event_features, logger,
        player_games=storage.get_all_player_game_stats(SPORT, since_date=since_date), leaders=LEADERS,
    )




def build_player_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Same incremental per-player walk as build_event_dataset."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    team_coordinates = _team_coordinates(storage, _team_ids(events))

    def player_features(game, prior, event, elo_ratings, own_previous_event_date, window):
        return build_player_features(game, prior, event, elo_ratings, own_previous_event_date, team_coordinates, window)

    return build_dataset_common.build_player_dataset(storage, SPORT, window, player_features, logger, since_date=since_date, events=events)


def build_ranking_dataset(storage: FeatureStorage, since_date: str | None = None) -> list[dict]:
    """Team-week granularity, for the National Ranking model. Each team's
    own season history grows unboundedly here since the ranking model
    uses season-to-date record/scoring/SOS rather than a trailing N-game
    average."""
    events = storage.get_all_events(SPORT, since_date=since_date)
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


_write_parquet = build_dataset_common.write_parquet


def _write_dataset(s3: S3Manager, key: str, rows: list[dict], label: str) -> None:
    build_dataset_common.write_dataset(s3, key, rows, label, logger)


_lookback_since_date = build_dataset_common.lookback_since_date


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    since_date = _lookback_since_date()

    logger.info(
        "Starting NCAAFB feature engineering (rolling window=%d games, since_date=%s)", window, since_date or "unbounded",
    )

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)

    logger.info("Building event-level dataset...")
    _write_dataset(s3, EVENT_FEATURES_KEY, build_event_dataset(storage, window, since_date=since_date), "event")

    logger.info("Building player-level dataset...")
    _write_dataset(s3, PLAYER_FEATURES_KEY, build_player_dataset(storage, window, since_date=since_date), "player")

    logger.info("Building team-week ranking dataset...")
    _write_dataset(s3, RANKING_FEATURES_KEY, build_ranking_dataset(storage, since_date=since_date), "ranking")

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    with xray.linked_segment_from_env(f"{SPORT}-feature-engineering"):
        main()
