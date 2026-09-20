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
from collections import defaultdict

from library.aws import xray
from library.aws.s3_manager import S3Manager
from library.features import build_dataset_common
from library.features.common import compute_elo_ratings
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


def _group_player_games_by_event_and_team(player_games: list[dict]) -> dict[tuple[str, str], list[dict]]:
    by_event_team: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for game in player_games:
        by_event_team[(game["event_key"], game["team_id"])].append(game)
    return by_event_team


_index_team_game_stats = build_dataset_common.index_team_game_stats


def _leader_and_history(
    player_games_by_event_team: dict[tuple[str, str], list[dict]],
    history: dict[str, list[dict]],
    identify_fn,
    event_key: str,
    team_id: str,
    window: int,
) -> tuple[dict | None, list[dict]]:
    """Identifies one team's leader at a position for this event via
    identify_fn (e.g. identify_starting_qb) and returns that player's
    prior history, most-recent-first and capped at `window`."""
    game = identify_fn(player_games_by_event_team.get((event_key, team_id), []))
    prior_games = history[game["entity_id"]][-window:][::-1] if game else []
    return game, prior_games


def build_event_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Walks events in a single chronological pass, growing each team's
    own history one game at a time, capped to the last `window` games.

    Each event's starting QB, lead rusher, and lead receiver get the same
    incremental treatment, keyed by player entity_id so a player's rolling
    history follows them across a trade; a team without an identifiable
    leader that game gets an empty history for that row."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    events = [e for e in events if is_real_franchise_matchup(e)]
    logger.info("Loaded %d completed events (excluding exhibition games)", len(events))

    player_games_by_event_team = _group_player_games_by_event_and_team(
        storage.get_all_player_game_stats(SPORT, since_date=since_date))
    team_game_stats_by_event_team = _index_team_game_stats(
        storage.get_all_team_game_stats(SPORT, since_date=since_date))

    elo_ratings, _ = compute_elo_ratings(events)  # only the pre-game side is used here
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))

    team_history: dict[str, list[dict]] = defaultdict(list)  # ascending, grows as we go
    qb_history: dict[str, list[dict]] = defaultdict(list)  # keyed by player entity_id, ascending
    rb_history: dict[str, list[dict]] = defaultdict(list)
    wr_history: dict[str, list[dict]] = defaultdict(list)
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
        row, leader_updates = _build_event_row(
            event, home_id, away_id, elo_ratings, window, team_history, qb_history, rb_history, wr_history,
            team_box_history, player_games_by_event_team,
        )
        rows.append(row)
        _update_event_history(
            event, home_id, away_id, team_history, team_box_history, team_game_stats_by_event_team, leader_updates,
        )

        if i % 500 == 0 or i == total:
            logger.info("Built event features: %d/%d", i, total)

    return rows


def _build_event_row(
    event: dict, home_id: str, away_id: str, elo_ratings: dict, window: int,
    team_history: dict, qb_history: dict, rb_history: dict, wr_history: dict, team_box_history: dict,
    player_games_by_event_team: dict,
) -> tuple[dict, list[tuple[dict | None, dict]]]:
    """This event's own feature row, plus [(leader_game_or_None,
    history_dict), ...] for _update_event_history to fold in after every
    row this event needs is built."""
    # Most-recent-first, capped at `window` -- O(window), not O(len(history)).
    home_history = team_history[home_id][-window:][::-1]
    away_history = team_history[away_id][-window:][::-1]
    home_box_history = team_box_history[home_id][-window:][::-1]
    away_box_history = team_box_history[away_id][-window:][::-1]

    event_key = event["event_key"]
    home_qb_game, home_qb_hist = _leader_and_history(
        player_games_by_event_team, qb_history, identify_starting_qb, event_key, home_id, window)
    away_qb_game, away_qb_hist = _leader_and_history(
        player_games_by_event_team, qb_history, identify_starting_qb, event_key, away_id, window)
    home_rb_game, home_rb_hist = _leader_and_history(
        player_games_by_event_team, rb_history, identify_lead_rusher, event_key, home_id, window)
    away_rb_game, away_rb_hist = _leader_and_history(
        player_games_by_event_team, rb_history, identify_lead_rusher, event_key, away_id, window)
    home_wr_game, home_wr_hist = _leader_and_history(
        player_games_by_event_team, wr_history, identify_lead_receiver, event_key, home_id, window)
    away_wr_game, away_wr_hist = _leader_and_history(
        player_games_by_event_team, wr_history, identify_lead_receiver, event_key, away_id, window)

    row = build_event_features(
        event, elo_ratings, home_history, away_history, window,
        home_qb_games=home_qb_hist, away_qb_games=away_qb_hist,
        home_rb_games=home_rb_hist, away_rb_games=away_rb_hist,
        home_wr_games=home_wr_hist, away_wr_games=away_wr_hist,
        home_team_box_stats=home_box_history, away_team_box_stats=away_box_history,
    )
    leader_updates = [
        (home_qb_game, qb_history), (away_qb_game, qb_history),
        (home_rb_game, rb_history), (away_rb_game, rb_history),
        (home_wr_game, wr_history), (away_wr_game, wr_history),
    ]
    return row, leader_updates


def _update_event_history(
    event: dict, home_id: str, away_id: str, team_history: dict, team_box_history: dict,
    team_game_stats_by_event_team: dict, leader_updates: list[tuple[dict | None, dict]],
) -> None:
    team_history[home_id].append(event)
    team_history[away_id].append(event)
    for game, history in leader_updates:
        if game:
            history[game["entity_id"]].append(game)

    event_key = event["event_key"]
    home_box_row = team_game_stats_by_event_team.get((event_key, home_id))
    away_box_row = team_game_stats_by_event_team.get((event_key, away_id))
    if home_box_row:
        team_box_history[home_id].append(home_box_row)
    if away_box_row:
        team_box_history[away_id].append(away_box_row)


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
        "Starting NFL feature engineering (rolling window=%d games, since_date=%s)", window, since_date or "unbounded",
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
    del player_rows  # free the ~150K-row list before the S3 upload
    s3.put_bytes(PLAYER_FEATURES_KEY, player_parquet, content_type="application/octet-stream")
    logger.info("Wrote %d player feature rows to s3://%s/%s", player_row_count, bucket, PLAYER_FEATURES_KEY)

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    with xray.linked_segment_from_env(f"{SPORT}-feature-engineering"):
        main()
