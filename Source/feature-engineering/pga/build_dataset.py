"""
PGA feature engineering. Pulls every completed PGA tournament from
DynamoDB (via FeatureStorage), builds each golfer's own rolling
performance history incrementally in a single chronological pass, and
writes THREE Parquet training datasets to S3 for the training Fargate
tasks to read: golfer_features.parquet (tournament grain -- top-10/top-5/
projected-score-to-par), round_features.parquet (golfer-round grain --
per-round score projection), and cutline_features.parquet (tournament
grain, no golfer dimension -- projected cut line). Also reads raw
season-stats snapshots directly from the raw data lake (RAW_BUCKET_NAME),
the only one of the three datasets that needs it -- the same "read a raw
non-DynamoDB S3 prefix at feature-engineering time" pattern NCAA MBB's
own AP-poll-based ranking dataset already uses.

Genuinely simpler than every head-to-head sport's own build_dataset.py:
there's no player_game_stats table to pull from -- a golfer's own past
results already live directly in events.participants (design/
DATA_SCHEMA.md), so this walks FeatureStorage.get_all_events("pga")
alone, the same call every head-to-head sport's build_player_dataset
makes to get PLAYER history, except here it doubles as the EVENT history
too, for all three datasets.

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds all three datasets from the current DynamoDB/
raw-bucket contents and overwrites the same three S3 keys.

Required environment variables:
    EVENTS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME
    RAW_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- tournaments of history each rolling
    average covers, see library.features.pga.DEFAULT_ROLLING_WINDOW.
    COURSE_HISTORY_WINDOW (default 5) -- past appearances AT THE SAME
    COURSE each course-fit/cut-line rolling average covers, see
    library.features.pga.DEFAULT_COURSE_HISTORY_WINDOW.

Usage:
    python build_dataset.py
"""
import io
import logging
import os
import re
from collections import defaultdict

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.features.pga import (
    DEFAULT_COURSE_HISTORY_WINDOW,
    SEASON_STAT_CATEGORIES,
    build_cutline_event_features,
    build_golfer_event_features,
    build_round_event_features,
)
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-feature-engineering")

SPORT = "pga"
GOLFER_FEATURES_KEY = "pga/training-data/golfer_features.parquet"
ROUND_FEATURES_KEY = "pga/training-data/round_features.parquet"
CUTLINE_FEATURES_KEY = "pga/training-data/cutline_features.parquet"

_STATISTICS_KEY_RE = re.compile(r"pga/statistics/(\d{8})\.json$")


def _load_season_stat_snapshots(raw_s3: S3Manager) -> list[dict]:
    """Every season-stats snapshot pga-ingest has written
    (pga/statistics/{date}.json) -- confirmed live, 2026-08-25, that
    ESPN's own golf/pga/statistics endpoint is CURRENT-SNAPSHOT-ONLY (its
    season/year query params are silently ignored), so this raw prefix is
    the ONLY possible source of historical values for these categories;
    there is no backfill path for it at all (design/DATA_SCHEMA.md), and
    a fresh deploy's dataset build will see zero snapshots here for a
    while. Returns [{"as_of_date", "value_by_category_and_athlete"}, ...],
    oldest first -- same overall shape/reasoning as NCAA MBB's own
    _load_rankings for its AP polls."""
    snapshots = []
    for key in raw_s3.list_keys("pga/statistics/"):
        match = _STATISTICS_KEY_RE.match(key)
        if not match:
            continue
        raw_date = match.group(1)
        as_of_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        payload = raw_s3.get_json(key)
        value_by_category_and_athlete: dict[str, dict[str, float]] = {}
        for category in payload.get("stats", {}).get("categories", []):
            name = category.get("name")
            if name not in SEASON_STAT_CATEGORIES:
                continue
            value_by_category_and_athlete[name] = {
                leader["athlete"]["id"]: leader["value"]
                for leader in category.get("leaders", [])
                if leader.get("athlete", {}).get("id") is not None and leader.get("value") is not None
            }
        snapshots.append({"as_of_date": as_of_date, "value_by_category_and_athlete": value_by_category_and_athlete})
    snapshots.sort(key=lambda s: s["as_of_date"])
    return snapshots


def _resolve_season_stats(snapshots: list[dict], entity_id: str, before_date: str) -> dict[str, float | None]:
    """This golfer's own values from the MOST RECENT snapshot STRICTLY
    BEFORE `before_date` -- never same-day-or-later, which would leak
    this tournament's own in-progress state into its own features. None
    for every category if no qualifying snapshot exists yet (true for
    100% of backfilled historical rows -- see _load_season_stat_
    snapshots' own docstring) or the golfer wasn't in that category's top
    50 that day."""
    candidates = [s for s in snapshots if s["as_of_date"] < before_date]
    if not candidates:
        return {category: None for category in SEASON_STAT_CATEGORIES}
    latest = candidates[-1]  # snapshots is ascending -> the last qualifying one is the most recent
    return {
        category: latest["value_by_category_and_athlete"].get(category, {}).get(entity_id)
        for category in SEASON_STAT_CATEGORIES
    }


def build_golfer_dataset(
    storage: FeatureStorage, window: int, course_window: int = DEFAULT_COURSE_HISTORY_WINDOW,
    season_stat_snapshots: list[dict] | None = None,
) -> list[dict]:
    """Walks completed tournaments in chronological order, growing each
    golfer's own result history one tournament at a time, capped to the
    last `window` starts -- plus a SEPARATE course-fit history keyed by
    (golfer, course_id), capped to the last `course_window` appearances
    specifically at that course (see library.features.pga.build_golfer_
    event_features's own docstring for why the same averaging function
    works unchanged for either input).

    Every participant's row is built from the SAME snapshot of history --
    a tournament's own field never sees any other golfer's result from
    that same tournament, only strictly earlier ones -- so this event's
    results are only folded into each golfer's history (both the overall
    one and the course-specific one) after every row for this event has
    already been built (the two loops below), not interleaved
    participant-by-participant.

    A course_id-less event (e.g. raw data captured before course_id was
    added to the schema -- design/DATA_SCHEMA.md) simply isn't folded
    into any course history and gets no course-fit signal of its own
    (course_results=None) -- it still gets full overall-history
    treatment, this only affects the course_* columns. season_stat_
    snapshots defaults to None (not []) so a caller with none loaded
    yet still gets every season_* column, just as an explicit missing
    value (_resolve_season_stats(None or [], ...) skipped entirely,
    same effect as an empty snapshot list)."""
    events = storage.get_all_events(SPORT)
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))
    logger.info("Loaded %d completed PGA tournaments", len(events_ascending))

    history: dict[str, list[dict]] = defaultdict(list)  # entity_id -> ascending list of result dicts
    course_history: dict[tuple[str, str], list[dict]] = defaultdict(list)  # (entity_id, course_id) -> ascending result dicts
    snapshots = season_stat_snapshots or []
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        course_id = event.get("course_id")
        participants = event.get("participants", [])
        for participant in participants:
            entity_id = participant["entity_id"]
            prior_results = history[entity_id][-window:][::-1]  # most-recent-first, capped at window
            course_results = (
                course_history[(entity_id, course_id)][-course_window:][::-1] if course_id is not None else None
            )
            season_stats = _resolve_season_stats(snapshots, entity_id, event["event_date"]) if snapshots else None
            rows.append(build_golfer_event_features(
                event, participant, prior_results, window, course_results, course_window, season_stats,
            ))

        for participant in participants:
            entity_id = participant["entity_id"]
            result = participant.get("result") or {}
            history[entity_id].append(result)
            if course_id is not None:
                course_history[(entity_id, course_id)].append(result)

        if i % 50 == 0 or i == total:
            logger.info("Built golfer features: %d/%d tournaments", i, total)

    return rows


def build_round_dataset(storage: FeatureStorage, window: int) -> list[dict]:
    """Round-level grain: one row per golfer per round ACTUALLY PLAYED
    (design/DATA_SCHEMA.md's participants[].result.rounds -- a cut
    golfer naturally contributes only rounds 1-2, never 3-4, no
    conditional cut-logic needed here, see library.features.pga.
    build_round_event_features's own docstring). Tracks a SECOND,
    round-number-scoped history (library.features.pga.rolling_round_
    averages) alongside the same overall per-golfer history build_golfer_
    dataset builds -- e.g. this golfer's own average ROUND 1 score across
    past tournaments, a genuinely different signal from their overall
    average (fast/slow starters, strong/weak closers).

    Same two-loop-per-tournament ordering discipline as build_golfer_
    dataset: every row for this tournament is built from history strictly
    BEFORE it, then this tournament's own rounds are folded into both
    histories only after every row is already built."""
    events = storage.get_all_events(SPORT)
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))
    logger.info("Loaded %d completed PGA tournaments for round-level features", len(events_ascending))

    history: dict[str, list[dict]] = defaultdict(list)  # entity_id -> ascending result dicts (tournament-level)
    round_history: dict[tuple[str, int], list[dict]] = defaultdict(list)  # (entity_id, round_number) -> ascending round dicts
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        participants = event.get("participants", [])
        for participant in participants:
            entity_id = participant["entity_id"]
            result = participant.get("result") or {}
            prior_overall = history[entity_id][-window:][::-1]
            for round_result in result.get("rounds", []):
                round_number = round_result["round"]
                prior_same_round = round_history[(entity_id, round_number)][-window:][::-1]
                rows.append(build_round_event_features(
                    event, participant, round_result, prior_overall, prior_same_round, window,
                ))

        for participant in participants:
            entity_id = participant["entity_id"]
            result = participant.get("result") or {}
            history[entity_id].append(result)
            for round_result in result.get("rounds", []):
                round_history[(entity_id, round_result["round"])].append(round_result)

        if i % 50 == 0 or i == total:
            logger.info("Built round features: %d/%d tournaments", i, total)

    return rows


def build_cutline_dataset(storage: FeatureStorage, course_window: int = DEFAULT_COURSE_HISTORY_WINDOW) -> list[dict]:
    """Tournament-level grain (no golfer dimension at all -- a cut line
    is a property of the whole field). Includes every completed Medal-
    scoring tournament, cut or not -- train_cutline_model.py filters to
    cut_count > 0 at TRAIN time, same "filter at train time, keep the raw
    dataset complete" convention NCAAFB's own national-ranking model uses
    for its own not-every-row-is-ranked case.

    Tracks course-level cut-score history (library.features.pga.
    build_cutline_event_features's own course_avg_cut_score) -- the one
    rolling signal that makes sense at this grain, since there's no
    golfer to build a per-golfer history from here."""
    events = storage.get_all_events(SPORT)
    events_ascending = sorted(events, key=lambda e: e.get("event_date", ""))
    logger.info("Loaded %d completed PGA tournaments for cut-line features", len(events_ascending))

    course_history: dict[str, list[float]] = defaultdict(list)  # course_id -> ascending cut_score values
    rows = []
    for event in events_ascending:
        course_id = event.get("course_id")
        prior_course_cut_scores = course_history[course_id][-course_window:][::-1] if course_id is not None else None
        rows.append(build_cutline_event_features(event, prior_course_cut_scores, course_window))
        if course_id is not None and event.get("cut_score") is not None:
            course_history[course_id].append(event["cut_score"])

    logger.info("Built cut-line features for %d tournaments", len(rows))
    return rows


def _write_parquet(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    df = pd.DataFrame(rows)
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def _write_dataset(s3: S3Manager, bucket: str, key: str, rows: list[dict], label: str) -> None:
    if not rows:
        raise RuntimeError(f"{label} produced 0 rows -- refusing to overwrite s3://{bucket}/{key} with an empty dataset")
    logger.info("Writing %d %s rows to Parquet...", len(rows), label)
    s3.put_bytes(key, _write_parquet(rows), content_type="application/octet-stream")
    logger.info("Wrote %d %s rows to s3://%s/%s", len(rows), label, bucket, key)


def main() -> None:
    window = int(os.environ.get("ROLLING_WINDOW", 5))
    course_window = int(os.environ.get("COURSE_HISTORY_WINDOW", DEFAULT_COURSE_HISTORY_WINDOW))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    raw_bucket = os.environ["RAW_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")

    logger.info(
        "Starting PGA feature engineering (rolling window=%d tournaments, course history window=%d appearances)",
        window, course_window,
    )

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)
    raw_s3 = S3Manager(raw_bucket, region=region)

    snapshots = _load_season_stat_snapshots(raw_s3)
    logger.info("Loaded %d season-stats snapshot(s)", len(snapshots))

    golfer_rows = build_golfer_dataset(storage, window, course_window, snapshots)
    _write_dataset(s3, bucket, GOLFER_FEATURES_KEY, golfer_rows, "golfer")

    round_rows = build_round_dataset(storage, window)
    _write_dataset(s3, bucket, ROUND_FEATURES_KEY, round_rows, "round")

    cutline_rows = build_cutline_dataset(storage, course_window)
    _write_dataset(s3, bucket, CUTLINE_FEATURES_KEY, cutline_rows, "cutline")

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    main()
