"""
NHL feature engineering. Pulls full history from DynamoDB (via
FeatureStorage), computes event-level, goalie-level and skater-level
training features using library.features.nhl_dataset's single
chronological pass, and writes three Parquet training datasets to S3 (via
S3Manager) for the training tasks to read.

Run by the training orchestrator, or manually via `aws ecs run-task`.
Safe to re-run at any time: it always rebuilds every dataset from the
current DynamoDB contents and overwrites the same three S3 keys.

Required environment variables:
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    TRAINING_LOOKBACK_SEASONS -- caps how far back FeatureStorage reads,
    via a since_date computed as roughly that many seasons before today.
    Unset reads full history.

Usage:
    python build_dataset.py
"""
import logging
import os

from library.aws import xray
from library.aws.s3_manager import S3Manager
from library.features import build_dataset_common
from library.features.nhl_dataset import build_datasets
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nhl-feature-engineering")

SPORT = "nhl"
EVENT_FEATURES_KEY = "nhl/training-data/event_features.parquet"
GOALIE_FEATURES_KEY = "nhl/training-data/goalie_features.parquet"
PLAYER_FEATURES_KEY = "nhl/training-data/player_features.parquet"


def build(storage: FeatureStorage, since_date: str | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    events = storage.get_all_events(SPORT, since_date=since_date)
    team_game_stats = storage.get_all_team_game_stats(SPORT, since_date=since_date)
    player_games = storage.get_all_player_game_stats(SPORT, since_date=since_date)
    logger.info(
        "Loaded %d completed events, %d team stat lines, %d player stat lines",
        len(events), len(team_game_stats), len(player_games),
    )
    return build_datasets(events, team_game_stats, player_games, logger)


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    since_date = build_dataset_common.lookback_since_date()
    logger.info("Starting NHL feature engineering (since_date=%s)", since_date or "unbounded")

    event_rows, goalie_rows, player_rows = build(FeatureStorage(), since_date=since_date)

    s3 = S3Manager(bucket, region=region)
    build_dataset_common.write_dataset(s3, EVENT_FEATURES_KEY, event_rows, "event", logger)
    build_dataset_common.write_dataset(s3, GOALIE_FEATURES_KEY, goalie_rows, "goalie", logger)
    build_dataset_common.write_dataset(s3, PLAYER_FEATURES_KEY, player_rows, "player", logger)
    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    with xray.linked_segment_from_env(f"{SPORT}-feature-engineering"):
        main()
