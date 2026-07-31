"""
NFL feature engineering. Pulls full history from DynamoDB (via
FeatureStorage), computes event-level and player-level training features
using library.features.nfl's pure functions, and writes two Parquet
training datasets to S3 (via S3Manager) for the training Fargate task to
read.

Not scheduled -- run manually via `aws ecs run-task`, same as
Source/data-backfills/nfl (see
Terraform/ecs-task-nfl-feature-engineering.tf). Safe to re-run at any
time: it always rebuilds both datasets from the current DynamoDB contents
and overwrites the same two S3 keys, there's no incremental/partial state
to worry about.

Required environment variables:
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- games of history each rolling average
    covers, see library.features.nfl.DEFAULT_ROLLING_WINDOW.

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
from library.features.nfl import build_event_features, build_player_features, compute_elo_ratings
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-feature-engineering")

SPORT = "nfl"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"
PLAYER_FEATURES_KEY = "nfl/training-data/player_features.parquet"


def build_event_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    """Walks events in a single chronological pass, growing each team's own
    history one game at a time, rather than re-filtering that team's whole
    history from scratch for every one of its games (which was O(games^2)
    per team -- fine for a team's ~20-game season, expensive once games
    span 10 years). Each team's running history stays capped to the last
    `window` games via a slice, so memory per team never grows past that
    regardless of how long the team's full history gets.
    """
    events = storage.get_all_events(SPORT)
    logger.info("Loaded %d completed events", len(events))

    elo_ratings = compute_elo_ratings(events)
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))

    team_history: dict[str, list[dict]] = defaultdict(list)  # ascending, grows as we go
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
        rows.append(build_event_features(event, elo_ratings, home_history, away_history, window))

        team_history[home_id].append(event)
        team_history[away_id].append(event)

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


def build_player_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    """Same incremental-history approach as build_event_dataset, per
    player instead of per team -- walks each player's games in
    chronological order once, growing their history one game at a time
    instead of re-filtering their whole career from scratch per game."""
    player_games = storage.get_all_player_game_stats()
    logger.info("Loaded %d player-game rows", len(player_games))

    games_by_player = _group_player_games_by_player(player_games)

    total = len(player_games)
    processed = 0
    rows = []
    for games in games_by_player.values():
        history: list[dict] = []  # ascending, grows as we go
        for game in games:
            prior = history[-window:][::-1]  # most-recent-first, capped at window
            rows.append(build_player_features(game, prior, window))
            history.append(game)

            processed += 1
            if processed % 20000 == 0 or processed == total:
                logger.info("Built player features: %d/%d", processed, total)

    return rows


def _write_parquet(rows: list[dict]) -> bytes:
    """Player feature rows don't share one fixed column set -- a QB's
    rolling stat averages and a kicker's never overlap (see
    library.features.nfl.rolling_player_stat_averages) -- pandas'
    DataFrame constructor already unions every row's keys and fills NaN
    for whichever rows are missing a given column, so no manual fieldname
    bookkeeping is needed here (CSV's DictWriter needed exactly that,
    which this replaces).

    Any dict-valued field (currently just label_stat_line) is JSON-encoded
    rather than stored as a native nested column -- Arrow structs need a
    consistent schema across rows, and label_stat_line's keys vary by
    player position, so a plain string column is the more robust fit.

    Builds the DataFrame directly from `rows` rather than a full
    intermediate copy, and only the dict-valued columns get touched
    afterward (a per-column .apply, not a second copy of every row).
    Which columns need it is read off just the first row -- every row
    shares the same schema, since they're all built by the same
    build_event_features/build_player_features call, so there's no need
    to scan the whole dataset to find out. A real ~150K-row run hit an
    OutOfMemoryError, and this full second copy -- alive at the same time
    as the original list, the DataFrame, and pyarrow's own Table during
    to_parquet() -- was the proximate cause.
    """
    if not rows:
        return b""
    df = pd.DataFrame(rows)
    dict_columns = [key for key, value in rows[0].items() if isinstance(value, dict)]
    for col in dict_columns:
        df[col] = df[col].apply(json.dumps)
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")

    logger.info("Starting NFL feature engineering (rolling window=%d games)", window)

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)

    logger.info("Building event-level dataset...")
    event_rows = build_event_dataset(storage, window)
    logger.info("Writing %d event feature rows to Parquet...", len(event_rows))
    s3.put_bytes(EVENT_FEATURES_KEY, _write_parquet(event_rows), content_type="application/octet-stream")
    logger.info("Wrote %d event feature rows to s3://%s/%s", len(event_rows), bucket, EVENT_FEATURES_KEY)

    logger.info("Building player-level dataset...")
    player_rows = build_player_dataset(storage, window)
    player_row_count = len(player_rows)
    logger.info("Writing %d player feature rows to Parquet...", player_row_count)
    player_parquet = _write_parquet(player_rows)
    del player_rows  # this is the ~150K-row list -- free it before the S3 upload, not just after
    s3.put_bytes(PLAYER_FEATURES_KEY, player_parquet, content_type="application/octet-stream")
    logger.info("Wrote %d player feature rows to s3://%s/%s", player_row_count, bucket, PLAYER_FEATURES_KEY)

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    main()
