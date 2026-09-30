"""
NFL feature engineering. Pulls full history from DynamoDB (via
FeatureStorage), computes event-level and player-level training features
using library.features.nfl's pure functions, and writes two Parquet
training datasets to S3 (via S3Manager) for the training Fargate task to
read.

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds both datasets from the current DynamoDB
contents and overwrites the same two S3 keys.

Required environment variables:
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- games of history each rolling average
    covers, see library.features.nfl.DEFAULT_ROLLING_WINDOW.
    TRAINING_LOOKBACK_SEASONS -- caps how far back FeatureStorage reads,
    via a since_date computed as roughly that many seasons before today.
    Unset (the default) reads full history, same as before this existed.
    Set by sfn-training-orchestrator.tf's RunFeatureEngineering override,
    from each sport's own training_lookback_seasons registry field
    (dynamodb-sport-registry.tf).

Usage:
    python build_dataset.py
"""
import logging
import os

from library.aws import xray
from library.aws.s3_manager import S3Manager
from library.features import build_dataset_common
from library.features.nfl import (
    build_event_features,
    build_player_features,
    identify_lead_receiver,
    identify_lead_rusher,
    identify_starting_qb,
)
from library.features.nfl_teams import is_real_franchise_matchup
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-feature-engineering")

SPORT = "nfl"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"
PLAYER_FEATURES_KEY = "nfl/training-data/player_features.parquet"


LEADERS = {"qb": identify_starting_qb, "rb": identify_lead_rusher, "wr": identify_lead_receiver}


def _event_features(
    event: dict, elo_ratings: dict, home_history: list[dict], away_history: list[dict], window: int, *,
    home_position_games: dict[str, list[dict]], away_position_games: dict[str, list[dict]], **box_stats,
) -> dict:
    """build_event_features with position games spread into its own
    home_qb_games/away_qb_games/... parameters."""
    return build_event_features(
        event, elo_ratings, home_history, away_history, window,
        **{f"home_{position}_games": games for position, games in home_position_games.items()},
        **{f"away_{position}_games": games for position, games in away_position_games.items()},
        **box_stats,
    )


def build_event_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Every event's feature row, each team's history and each team's
    starting QB, lead rusher and lead receiver's history capped to the
    last `window` games; a team without an identifiable leader that game
    gets an empty history for that row."""
    events = [e for e in storage.get_all_events(SPORT, since_date=since_date) if is_real_franchise_matchup(e)]
    logger.info("Loaded %d completed events (excluding exhibition games)", len(events))
    return build_dataset_common.build_team_event_rows(
        events, storage.get_all_team_game_stats(SPORT, since_date=since_date), window, _event_features, logger,
        player_games=storage.get_all_player_game_stats(SPORT, since_date=since_date), leaders=LEADERS,
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
        "Starting NFL feature engineering (rolling window=%d games, since_date=%s)", window, since_date or "unbounded",
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
