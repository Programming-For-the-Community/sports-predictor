"""
NBA feature engineering. Pulls full history from DynamoDB (via
FeatureStorage), computes event-level and player-level training features
using library.features.nba's pure functions, and writes two Parquet
training datasets to S3 (via S3Manager) for the training Fargate task to
read.

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds both datasets from the current DynamoDB
contents and overwrites the same two S3 keys.

Produces two datasets: team-level event features and player-level
features.

Required environment variables:
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- games of history each rolling average
    covers, see library.features.common.DEFAULT_ROLLING_WINDOW.
    TRAINING_LOOKBACK_SEASONS -- caps how far back FeatureStorage reads,
    via a since_date computed as roughly that many seasons before today.
    Unset (the default) reads full history, same as before this existed.

Usage:
    python build_dataset.py
"""
import logging
import os

from library.aws import xray
from library.aws.s3_manager import S3Manager
from library.features import build_dataset_common
from library.features.nba import build_event_features, build_player_features
from library.features.nba_teams import is_real_franchise_matchup
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nba-feature-engineering")

SPORT = "nba"
EVENT_FEATURES_KEY = "nba/training-data/event_features.parquet"
PLAYER_FEATURES_KEY = "nba/training-data/player_features.parquet"


def build_event_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    events = [e for e in storage.get_all_events(SPORT, since_date=since_date) if is_real_franchise_matchup(e)]
    logger.info("Loaded %d completed events (excluding exhibition games)", len(events))
    return build_dataset_common.build_team_event_rows(
        events, storage.get_all_team_game_stats(SPORT, since_date=since_date), window, build_event_features, logger,
    )


def build_player_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    return build_dataset_common.build_player_dataset(
        storage, SPORT, window, build_player_features, logger,
        since_date=since_date, filter_fn=is_real_franchise_matchup,
    )


_write_parquet = build_dataset_common.write_parquet
_lookback_since_date = build_dataset_common.lookback_since_date


def _write_dataset(s3: S3Manager, key: str, rows: list[dict], label: str) -> None:
    build_dataset_common.write_dataset(s3, key, rows, label, logger)


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    since_date = _lookback_since_date()

    logger.info(
        "Starting NBA feature engineering (rolling window=%d games, since_date=%s)", window, since_date or "unbounded",
    )

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)

    logger.info("Building event-level dataset...")
    _write_dataset(s3, EVENT_FEATURES_KEY, build_event_dataset(storage, window, since_date=since_date), "event")

    logger.info("Building player-level dataset...")
    _write_dataset(s3, PLAYER_FEATURES_KEY, build_player_dataset(storage, window, since_date=since_date), "player")

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    with xray.linked_segment_from_env(f"{SPORT}-feature-engineering"):
        main()
