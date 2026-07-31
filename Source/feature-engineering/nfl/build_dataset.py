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


def _group_events_by_team(events: list[dict]) -> dict[str, list[dict]]:
    """Each team's own events, sorted ascending by date -- ascending so
    "every game before this date" is a simple prefix filter."""
    by_team: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        for participant in event.get("participants", []):
            entity_id = participant.get("entity_id")
            if entity_id:
                by_team[entity_id].append(event)
    for team_events in by_team.values():
        team_events.sort(key=lambda e: e.get("event_date", ""))
    return by_team


def _history_before(sorted_ascending: list[dict], event_date: str) -> list[dict]:
    """sorted_ascending must already be sorted oldest-first. Returns prior
    rows, most recent first -- matching FeatureStorage.get_team_events'/
    get_player_game_stats' contract, which build_event_features/
    build_player_features expect."""
    prior = [row for row in sorted_ascending if row.get("event_date", "") < event_date]
    prior.reverse()
    return prior


def build_event_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    events = storage.get_all_events(SPORT)
    logger.info("Loaded %d completed events", len(events))

    elo_ratings = compute_elo_ratings(events)
    events_by_team = _group_events_by_team(events)

    rows = []
    for event in events:
        participants = event.get("participants", [])
        home = next((p for p in participants if p.get("role") == "home"), None)
        away = next((p for p in participants if p.get("role") == "away"), None)
        if home is None or away is None:
            logger.debug("Skipping event %s -- missing home/away role", event.get("event_key"))
            continue

        home_history = _history_before(events_by_team[home["entity_id"]], event["event_date"])
        away_history = _history_before(events_by_team[away["entity_id"]], event["event_date"])
        rows.append(build_event_features(event, elo_ratings, home_history, away_history, window))

    return rows


def _group_player_games_by_player(player_games: list[dict]) -> dict[str, list[dict]]:
    by_player: dict[str, list[dict]] = defaultdict(list)
    for game in player_games:
        by_player[game["entity_id"]].append(game)
    for games in by_player.values():
        games.sort(key=lambda g: g.get("event_date", ""))
    return by_player


def build_player_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    player_games = storage.get_all_player_game_stats()
    logger.info("Loaded %d player-game rows", len(player_games))

    games_by_player = _group_player_games_by_player(player_games)

    rows = []
    for game in player_games:
        history = games_by_player[game["entity_id"]]
        prior = _history_before(history, game.get("event_date", ""))
        rows.append(build_player_features(game, prior, window))

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
    first rather than stored as a native nested column -- Arrow structs
    need a consistent schema across rows, and label_stat_line's keys vary
    by player position, so a plain string column is the more robust fit.
    """
    if not rows:
        return b""
    encoded_rows = [
        {key: json.dumps(value) if isinstance(value, dict) else value for key, value in row.items()}
        for row in rows
    ]
    buffer = io.BytesIO()
    pd.DataFrame(encoded_rows).to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)

    event_rows = build_event_dataset(storage, window)
    s3.put_bytes(EVENT_FEATURES_KEY, _write_parquet(event_rows), content_type="application/octet-stream")
    logger.info("Wrote %d event feature rows to s3://%s/%s", len(event_rows), bucket, EVENT_FEATURES_KEY)

    player_rows = build_player_dataset(storage, window)
    s3.put_bytes(PLAYER_FEATURES_KEY, _write_parquet(player_rows), content_type="application/octet-stream")
    logger.info("Wrote %d player feature rows to s3://%s/%s", len(player_rows), bucket, PLAYER_FEATURES_KEY)


if __name__ == "__main__":
    main()
