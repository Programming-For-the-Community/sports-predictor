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


def _index_team_game_stats(team_game_stats: list[dict]) -> dict[tuple[str, str], dict]:
    # One row per (event, team), unlike player games -- a direct lookup,
    # not a list to pick a leader from.
    return {(row["event_key"], row["team_id"]): row for row in team_game_stats}


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


def build_event_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    """Walks events in a single chronological pass, growing each team's
    own history one game at a time, capped to the last `window` games.

    Each event's starting QB, lead rusher, and lead receiver get the same
    incremental treatment, keyed by player entity_id so a player's rolling
    history follows them across a trade; a team without an identifiable
    leader that game gets an empty history for that row."""
    events = storage.get_all_events(SPORT)
    events = [e for e in events if is_real_franchise_matchup(e)]
    logger.info("Loaded %d completed events (excluding exhibition games)", len(events))

    player_games_by_event_team = _group_player_games_by_event_and_team(storage.get_all_player_game_stats(SPORT))
    team_game_stats_by_event_team = _index_team_game_stats(storage.get_all_team_game_stats(SPORT))

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
        # Most-recent-first, capped at `window` -- O(window), not O(len(history)).
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
            event, elo_ratings, home_history, away_history, window,
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
    """Maps (team_id, event_key) -> that team's previous event's date."""
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
    """Same incremental-history approach as build_event_dataset, per
    player instead of per team."""
    events = storage.get_all_events(SPORT)
    events = [e for e in events if is_real_franchise_matchup(e)]
    events_by_key = {event["event_key"]: event for event in events}
    elo_ratings, _ = compute_elo_ratings(events)  # only the pre-game side is used here
    team_previous_event_dates = _team_previous_event_dates(events)

    player_games = storage.get_all_player_game_stats(SPORT)
    logger.info("Loaded %d player-game rows", len(player_games))

    games_by_player = _group_player_games_by_player(player_games)

    total = len(player_games)
    seen = 0  # rows examined, including skipped ones
    skipped = 0
    rows = []
    for games in games_by_player.values():
        history: list[dict] = []  # ascending, grows as we go
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
                prior = history[-window:][::-1]  # most-recent-first, capped at window
                own_previous_event_date = team_previous_event_dates.get((game["team_id"], game["event_key"]))
                rows.append(build_player_features(game, prior, event, elo_ratings, own_previous_event_date, window))
                history.append(game)

            seen += 1
            if seen % 20000 == 0 or seen == total:
                logger.info("Built player features: %d/%d (%d skipped)", seen, total, skipped)

    return rows


def _write_parquet(rows: list[dict]) -> bytes:
    """Player feature rows don't share one fixed column set (e.g. a QB's
    and a kicker's rolling stat averages never overlap); pandas' DataFrame
    constructor unions every row's keys and fills NaN for missing columns.

    Dict-valued fields (currently just label_stat_line) are JSON-encoded
    before writing, since Parquet/Arrow needs a consistent schema across
    rows and label_stat_line's keys vary by player position."""
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
    if not event_rows:
        raise RuntimeError(
            "build_event_dataset produced 0 rows -- refusing to overwrite "
            f"s3://{bucket}/{EVENT_FEATURES_KEY} with an empty dataset",
        )
    logger.info("Writing %d event feature rows to Parquet...", len(event_rows))
    s3.put_bytes(EVENT_FEATURES_KEY, _write_parquet(event_rows), content_type="application/octet-stream")
    logger.info("Wrote %d event feature rows to s3://%s/%s", len(event_rows), bucket, EVENT_FEATURES_KEY)

    logger.info("Building player-level dataset...")
    player_rows = build_player_dataset(storage, window)
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
    main()
