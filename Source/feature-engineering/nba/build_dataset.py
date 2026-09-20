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
from collections import defaultdict

from library.aws import xray
from library.aws.s3_manager import S3Manager
from library.features import build_dataset_common
from library.features.common import compute_elo_ratings
from library.features.nba import build_event_features, build_player_features
from library.features.nba_teams import is_real_franchise_matchup
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nba-feature-engineering")

SPORT = "nba"
EVENT_FEATURES_KEY = "nba/training-data/event_features.parquet"
PLAYER_FEATURES_KEY = "nba/training-data/player_features.parquet"


_index_team_game_stats = build_dataset_common.index_team_game_stats


def build_event_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Walks events in a single chronological pass, growing each team's
    own history one game at a time rather than re-filtering that team's
    whole history for every game."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    events = [e for e in events if is_real_franchise_matchup(e)]
    logger.info("Loaded %d completed events (excluding exhibition games)", len(events))

    team_game_stats_by_event_team = _index_team_game_stats(
        storage.get_all_team_game_stats(SPORT, since_date=since_date))

    elo_ratings, _ = compute_elo_ratings(events)  # only the pre-game side is used here
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))

    team_history: dict[str, list[dict]] = defaultdict(list)  # ascending, grows as we go
    team_box_history: dict[str, list[dict]] = defaultdict(list)  # keyed by team_id, ascending
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
        # Most-recent-first, capped at `window` -- O(window), not O(len(history)).
        home_history = team_history[home_id][-window:][::-1]
        away_history = team_history[away_id][-window:][::-1]
        home_box_history = team_box_history[home_id][-window:][::-1]
        away_box_history = team_box_history[away_id][-window:][::-1]

        rows.append(build_event_features(
            event, elo_ratings, home_history, away_history, window,
            home_team_box_stats=home_box_history, away_team_box_stats=away_box_history,
        ))

        team_history[home_id].append(event)
        team_history[away_id].append(event)

        event_key = event["event_key"]
        home_box_row = team_game_stats_by_event_team.get((event_key, home_id))
        away_box_row = team_game_stats_by_event_team.get((event_key, away_id))
        if home_box_row:
            team_box_history[home_id].append(home_box_row)
        if away_box_row:
            team_box_history[away_id].append(away_box_row)

        if i % 500 == 0 or i == total:
            logger.info("Built event features: %d/%d", i, total)

    return rows


def build_player_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    return build_dataset_common.build_player_dataset(
        storage, SPORT, window, build_player_features, logger,
        since_date=since_date, filter_fn=is_real_franchise_matchup,
    )


_write_parquet = build_dataset_common.write_parquet
_lookback_since_date = build_dataset_common.lookback_since_date


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
    event_rows = build_event_dataset(storage, window, since_date=since_date)
    if not event_rows:
        raise RuntimeError(
            "build_event_dataset produced 0 rows -- refusing to overwrite "
            f"s3://{bucket}/{EVENT_FEATURES_KEY} with an empty dataset",
        )
    logger.info("Writing %d event feature rows to Parquet...", len(event_rows))
    s3.put_bytes(EVENT_FEATURES_KEY, _write_parquet(event_rows), content_type="application/octet-stream")
    logger.info("Wrote %d event feature rows to s3://%s/%s", len(event_rows), bucket, EVENT_FEATURES_KEY)

    logger.info("Building player-level dataset...")
    player_rows = build_player_dataset(storage, window, since_date=since_date)
    player_row_count = len(player_rows)
    if not player_row_count:
        raise RuntimeError(
            "build_player_dataset produced 0 rows -- refusing to overwrite "
            f"s3://{bucket}/{PLAYER_FEATURES_KEY} with an empty dataset",
        )
    logger.info("Writing %d player feature rows to Parquet...", player_row_count)
    player_parquet = _write_parquet(player_rows)
    del player_rows  # free the large list before the S3 upload, not just after
    s3.put_bytes(PLAYER_FEATURES_KEY, player_parquet, content_type="application/octet-stream")
    logger.info("Wrote %d player feature rows to s3://%s/%s", player_row_count, bucket, PLAYER_FEATURES_KEY)

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    with xray.linked_segment_from_env(f"{SPORT}-feature-engineering"):
        main()
