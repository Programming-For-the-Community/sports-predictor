"""
NCAA MBB feature engineering. Pulls full history from DynamoDB (via
FeatureStorage), computes event-level and player-level training features
using library.features.ncaambb's pure functions, and writes two Parquet
training datasets to S3 (via S3Manager) for the training Fargate task to
read.

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds both datasets from the current DynamoDB
contents and overwrites the same two S3 keys.

Produces three datasets: team-level event features, player-level
features, and team-poll features (the National Ranking model). Unlike
NBA's own build_dataset.py, there's no is_real_franchise_matchup-equivalent
filter here -- NCAA MBB has no exhibition/All-Star-style games to exclude
(no preseason concept exists at all -- see aws-lambdas/ncaambb/ingest/
handler.py's own docstring), and NIT/secondary-postseason-tournament
games are deliberately included as real training data, not filtered out
-- a real competitive elimination tournament, unlike an exhibition.

The ranking dataset (build_ranking_dataset) is poll-centric, not
event-centric like NCAAFB's own equivalent -- see
library.features.ncaambb.build_team_week_features's own docstring for why
(AP-poll data lives in its own S3 prefix, not attached to event dicts).
Reads raw AP polls directly from the raw data lake bucket (RAW_BUCKET_NAME),
the only dataset here that needs it -- the other two only ever touch
DynamoDB and the model artifacts bucket.

Required environment variables:
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME
    RAW_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- games of history each rolling average
    covers, see library.features.common.DEFAULT_ROLLING_WINDOW.
    TRAINING_LOOKBACK_SEASONS -- caps how far back FeatureStorage reads
    (and, for the ranking dataset, how far back AP polls are kept), via a
    since_date computed as roughly that many seasons before today. Unset
    (the default) reads full history, same as before this existed.

Usage:
    python build_dataset.py
"""
import io
import json
import logging
import os
import re
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.features.common import compute_elo_ratings
from library.features.ncaambb import build_event_features, build_player_features, build_team_week_features
from library.http.ncaambb_core import ap_poll_to_rank_by_team
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaambb-feature-engineering")

SPORT = "ncaambb"
EVENT_FEATURES_KEY = "ncaambb/training-data/event_features.parquet"
PLAYER_FEATURES_KEY = "ncaambb/training-data/player_features.parquet"
RANKING_FEATURES_KEY = "ncaambb/training-data/ranking_features.parquet"

# Matches the raw key shape data-backfills/ncaambb/backfill.py's own
# seed_rankings writes: ncaambb/rankings/{season}/{season_type}/{week}.json.
_RANKING_KEY_RE = re.compile(r"^ncaambb/rankings/(\d+)/(\d+)/(\d+)\.json$")


def _index_team_game_stats(team_game_stats: list[dict]) -> dict[tuple[str, str], dict]:
    # One row per (event, team), keyed for direct lookup.
    return {(row["event_key"], row["team_id"]): row for row in team_game_stats}


def build_event_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Walks events in a single chronological pass, growing each team's
    own history one game at a time rather than re-filtering that team's
    whole history for every game."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    logger.info("Loaded %d completed events", len(events))

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


def build_player_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Same incremental-history approach as build_event_dataset, per
    player instead of per team."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    events_by_key = {event["event_key"]: event for event in events}
    elo_ratings, _ = compute_elo_ratings(events)  # only the pre-game side is used here
    team_previous_event_dates = _team_previous_event_dates(events)

    player_games = storage.get_all_player_game_stats(SPORT, since_date=since_date)
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


def _load_rankings(raw_s3: S3Manager, since_date: str | None = None) -> list[dict]:
    """Every AP poll data-backfills/ncaambb/backfill.py's seed_rankings
    (or daily ingest's own current-poll fetch) wrote to S3. Returns
    [{"season", "season_type", "week", "as_of_date", "rank_by_team"}, ...],
    oldest first. as_of_date is the poll's own release date (truncated to
    YYYY-MM-DD, same convention as event_date elsewhere), not derived
    from the season/week path components. since_date (inclusive) drops
    polls released before it -- applied here in Python, not at the S3
    listing level, since raw poll keys aren't date-sortable by prefix."""
    polls = []
    for key in raw_s3.list_keys("ncaambb/rankings/"):
        match = _RANKING_KEY_RE.match(key)
        if not match:
            continue
        season, season_type, week = (int(group) for group in match.groups())
        poll = raw_s3.get_json(key)
        as_of_date = (poll.get("date") or "")[:10]
        if not as_of_date:
            logger.debug("Skipping %s -- no date on the raw poll", key)
            continue
        if since_date is not None and as_of_date < since_date:
            continue
        polls.append({
            "season": season, "season_type": season_type, "week": week,
            "as_of_date": as_of_date, "rank_by_team": ap_poll_to_rank_by_team(poll),
        })
    polls.sort(key=lambda p: p["as_of_date"])
    return polls


def _team_pre_rating(event: dict, team_id: str, elo_ratings: dict[str, dict[str, float]]) -> float | None:
    ratings = elo_ratings.get(event["event_key"], {})
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    if home is not None and home.get("entity_id") == team_id:
        return ratings.get("home_pre_rating")
    return ratings.get("away_pre_rating")


def _resolve_own_elo(
    prior_events: list[dict], season_events: list[dict], as_of_date: str, team_id: str,
    elo_ratings: dict[str, dict[str, float]],
) -> float | None:
    """A team's Elo "as of" an arbitrary poll date, approximated -- Elo
    ratings only exist as pre-game snapshots (the rating entering a
    SPECIFIC event), not as a continuous as-of-any-date series, so this
    picks the nearest available anchor: prefers the rating entering the
    team's most recent event on/before as_of_date (their rating heading
    into that game -- NOTE this does not yet reflect that game's own
    result, understating true current strength by up to one game's worth
    of rating movement, a known, accepted approximation given Elo has no
    cheaper source of a true point-in-time value); falls back to the
    rating entering their earliest event strictly after as_of_date (e.g.
    a preseason poll released before the team's first game of the
    season). None if the team has no events in this season at all yet."""
    if prior_events:
        return _team_pre_rating(prior_events[-1], team_id, elo_ratings)  # ascending -> most recent prior event
    future_events = [e for e in season_events if e.get("event_date", "") >= as_of_date]
    if not future_events:
        return None
    return _team_pre_rating(future_events[0], team_id, elo_ratings)


def build_ranking_dataset(storage: FeatureStorage, raw_s3: S3Manager, since_date: str | None = None) -> list[dict]:
    """Team-poll granularity, for the National Ranking model. Unlike
    NCAAFB's own build_ranking_dataset (one row per team per EVENT,
    ranked or not, filtered to ranked-only at train time), this only
    builds rows for teams a poll actually ranked -- D1's ~362 teams vs.
    NCAAFB's ~130 FBS teams means building an unranked row for every team
    at every poll would be mostly wasted work (only ~25 of 362 teams are
    ever ranked by any one poll)."""
    events = storage.get_all_events(SPORT, since_date=since_date)
    elo_ratings, _ = compute_elo_ratings(events)  # only the pre-game side is used here

    events_by_team: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        for participant in event.get("participants", []):
            events_by_team[participant["entity_id"]].append(event)
    for team_events in events_by_team.values():
        team_events.sort(key=lambda e: e.get("event_date", ""))  # ascending

    polls = _load_rankings(raw_s3, since_date=since_date)
    logger.info("Loaded %d AP polls", len(polls))

    rows = []
    for poll in polls:
        for team_id, rank in poll["rank_by_team"].items():
            season_events = [e for e in events_by_team.get(team_id, []) if e.get("season") == poll["season"]]
            prior_events = [e for e in season_events if e.get("event_date", "") < poll["as_of_date"]]
            own_elo = _resolve_own_elo(prior_events, season_events, poll["as_of_date"], team_id, elo_ratings)
            rows.append(build_team_week_features(
                team_id, poll["as_of_date"], poll["season"], own_elo,
                prior_events[::-1],  # most-recent-first
                elo_ratings, rank,
            ))

    return rows


def _write_parquet(rows: list[dict]) -> bytes:
    """JSON-encodes dict-valued columns (e.g. label_stat_line) before
    writing, since Parquet doesn't support raw dict values."""
    if not rows:
        return b""
    df = pd.DataFrame(rows)
    dict_columns = [key for key, value in rows[0].items() if isinstance(value, dict)]
    for col in dict_columns:
        df[col] = df[col].apply(json.dumps)
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def _lookback_since_date() -> str | None:
    """Converts TRAINING_LOOKBACK_SEASONS (a season count) into an
    approximate since_date FeatureStorage's GSI queries can filter on --
    unset (the common case today) means unbounded, same as before this
    existed. 366 days/season is deliberately generous (never trims a
    genuinely in-window season for being a day short)."""
    lookback = os.environ.get("TRAINING_LOOKBACK_SEASONS")
    if not lookback:
        return None
    return (date.today() - timedelta(days=int(lookback) * 366)).isoformat()


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    raw_bucket = os.environ["RAW_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    since_date = _lookback_since_date()

    logger.info(
        "Starting NCAA MBB feature engineering (rolling window=%d games, since_date=%s)",
        window, since_date or "unbounded",
    )

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)
    raw_s3 = S3Manager(raw_bucket, region=region)

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

    logger.info("Building ranking dataset...")
    ranking_rows = build_ranking_dataset(storage, raw_s3, since_date=since_date)
    if not ranking_rows:
        raise RuntimeError(
            "build_ranking_dataset produced 0 rows -- refusing to overwrite "
            f"s3://{bucket}/{RANKING_FEATURES_KEY} with an empty dataset",
        )
    logger.info("Writing %d ranking feature rows to Parquet...", len(ranking_rows))
    s3.put_bytes(RANKING_FEATURES_KEY, _write_parquet(ranking_rows), content_type="application/octet-stream")
    logger.info("Wrote %d ranking feature rows to s3://%s/%s", len(ranking_rows), bucket, RANKING_FEATURES_KEY)

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    main()
