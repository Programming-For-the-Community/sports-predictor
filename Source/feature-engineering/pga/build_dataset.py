"""
PGA feature engineering. Pulls every completed PGA tournament from
DynamoDB (via FeatureStorage), builds each golfer's own rolling
performance history incrementally in a single chronological pass, and
writes FIVE Parquet training datasets to S3 for the training Fargate
tasks to read: golfer_features.parquet (tournament grain -- top-10/top-5/
projected-score-to-par), round_features.parquet (golfer-round grain --
per-round score projection), cutline_features.parquet (tournament grain,
no golfer dimension -- projected cut line), match_features.parquet
(individual-match grain -- match win probability, Ryder Cup/Presidents
Cup/WGC Match Play), and cup_features.parquet (Cup grain -- team win
probability, Ryder Cup/Presidents Cup only). Also reads raw season-stats
snapshots directly from the raw data lake (RAW_BUCKET_NAME), the only one
of the datasets that needs it -- the same "read a raw non-DynamoDB S3
prefix at feature-engineering time" pattern NCAA MBB's own AP-poll-based
ranking dataset already uses.

Genuinely simpler than every head-to-head sport's own build_dataset.py:
there's no player_game_stats table to pull from -- a golfer's own past
results already live directly in events.participants (design/
DATA_SCHEMA.md), so this walks FeatureStorage.get_all_events("pga")
alone, the same call every head-to-head sport's build_player_dataset
makes to get PLAYER history, except here it doubles as the EVENT history
too, for every dataset (see build_match_and_cup_datasets' own docstring
for how match_play/cup events read that same history without feeding it).

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds all five datasets from the current DynamoDB/
raw-bucket contents and overwrites the same five S3 keys.

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
from collections import defaultdict

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.features.pga import (
    DEFAULT_COURSE_HISTORY_WINDOW,
    build_cup_event_features,
    build_cutline_event_features,
    build_golfer_event_features,
    build_match_event_features,
    build_round_event_features,
)
from library.storage.feature_storage import FeatureStorage
from library.storage.pga_season_stats import load_season_stat_snapshots, resolve_season_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pga-feature-engineering")

SPORT = "pga"
GOLFER_FEATURES_KEY = "pga/training-data/golfer_features.parquet"
ROUND_FEATURES_KEY = "pga/training-data/round_features.parquet"
CUTLINE_FEATURES_KEY = "pga/training-data/cutline_features.parquet"
MATCH_FEATURES_KEY = "pga/training-data/match_features.parquet"
CUP_FEATURES_KEY = "pga/training-data/cup_features.parquet"


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
    value (resolve_season_stats(None or [], ...) skipped entirely,
    same effect as an empty snapshot list)."""
    # event_type == "field" only -- get_all_events(SPORT) now also
    # returns "match_play"/"cup" rows (library/normalize/pga_matchplay.py)
    # once any Ryder Cup/Presidents Cup/WGC Match Play data has been
    # normalized. Those don't carry stroke scores/cut fields/a real
    # golfer-count participants list at all, so processing them here
    # unfiltered would silently corrupt this dataset (e.g. a "cup" event's
    # 2-team participants list would read as a 2-golfer field_size).
    events = [e for e in storage.get_all_events(SPORT) if e.get("event_type") == "field"]
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
            season_stats = resolve_season_stats(snapshots, entity_id, event["event_date"]) if snapshots else None
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
    # event_type == "field" only -- get_all_events(SPORT) now also
    # returns "match_play"/"cup" rows (library/normalize/pga_matchplay.py)
    # once any Ryder Cup/Presidents Cup/WGC Match Play data has been
    # normalized. Those don't carry stroke scores/cut fields/a real
    # golfer-count participants list at all, so processing them here
    # unfiltered would silently corrupt this dataset (e.g. a "cup" event's
    # 2-team participants list would read as a 2-golfer field_size).
    events = [e for e in storage.get_all_events(SPORT) if e.get("event_type") == "field"]
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
    # event_type == "field" only -- get_all_events(SPORT) now also
    # returns "match_play"/"cup" rows (library/normalize/pga_matchplay.py)
    # once any Ryder Cup/Presidents Cup/WGC Match Play data has been
    # normalized. Those don't carry stroke scores/cut fields/a real
    # golfer-count participants list at all, so processing them here
    # unfiltered would silently corrupt this dataset (e.g. a "cup" event's
    # 2-team participants list would read as a 2-golfer field_size).
    events = [e for e in storage.get_all_events(SPORT) if e.get("event_type") == "field"]
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


def build_match_and_cup_datasets(storage: FeatureStorage, window: int) -> tuple[list[dict], list[dict]]:
    """Match win-probability rows (event_type "match_play") and Cup
    (team) win-probability rows (event_type "cup") -- built together
    because they share one chronological history walk.

    PASS 1 derives each Cup's own FULL roster (every golfer who played
    ANY session, not just one match) by scanning every match_play event's
    own golfer_entity_ids, grouped by (parent_event_id, role) -- a Cup
    event's own participants only carry the two teams' final point
    totals (library/normalize/pga_matchplay.py), not a player list, so
    this is the only way to know who to average form over for the Cup
    dataset. Independent of chronological order -- a roster is fixed
    before the tournament starts regardless of which session's matches
    get walked first.

    PASS 2 is a SINGLE walk over field ("regular" stroke-play, including
    Zurich Classic) + match_play + cup events together, sorted by
    event_date, growing each golfer's own STROKE-PLAY history exactly
    the way build_golfer_dataset does (only field events feed it --
    match/Cup RESULTS are never folded in, since a won/lost/halved match
    isn't a stroke score and there isn't enough match-play history per
    golfer for a separate "match-play form" signal to mean anything
    yet). A match_play or cup event READS the current history snapshot
    for its own participants' golfers (build_match_event_features/
    build_cup_event_features average across 1+ golfers per side) but
    never WRITES into it.

    A cup event sorts at its own tournament-level start date -- the same
    date as its OWN Thursday session, i.e. strictly BEFORE any of that
    Cup's own matches are walked -- which is the correct behavior for a
    Cup-outcome prediction (pre-tournament form only, not results that
    happened mid-tournament)."""
    events = storage.get_all_events(SPORT)
    field_events = [e for e in events if e.get("event_type") == "field"]
    match_events = [e for e in events if e.get("event_type") == "match_play"]
    cup_events = [e for e in events if e.get("event_type") == "cup"]
    logger.info(
        "Loaded %d field, %d match-play, and %d cup event(s) for match/cup features",
        len(field_events), len(match_events), len(cup_events),
    )

    cup_rosters: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for match_event in match_events:
        parent_id = match_event.get("parent_event_id")
        if parent_id is None:
            continue
        for participant in match_event.get("participants", []):
            role = participant.get("role")
            if role is None:
                continue
            cup_rosters[parent_id][role].update(participant.get("golfer_entity_ids", []))

    timeline = sorted(field_events + match_events + cup_events, key=lambda e: e.get("event_date", ""))
    history: dict[str, list[dict]] = defaultdict(list)
    match_rows, cup_rows = [], []
    for event in timeline:
        event_type = event.get("event_type")
        if event_type == "field":
            for participant in event.get("participants", []):
                history[participant["entity_id"]].append(participant.get("result") or {})
            continue

        participants = event.get("participants", [])
        home = next((p for p in participants if p.get("role") == "home"), None)
        away = next((p for p in participants if p.get("role") == "away"), None)
        if home is None or away is None:
            continue

        if event_type == "match_play":
            home_prior = {gid: history[gid][-window:][::-1] for gid in home.get("golfer_entity_ids", [])}
            away_prior = {gid: history[gid][-window:][::-1] for gid in away.get("golfer_entity_ids", [])}
            match_rows.append(build_match_event_features(event, home_prior, away_prior, window))
        elif event_type == "cup":
            roster = cup_rosters.get(event["event_id"], {})
            home_prior = {gid: history[gid][-window:][::-1] for gid in roster.get("home", set())}
            away_prior = {gid: history[gid][-window:][::-1] for gid in roster.get("away", set())}
            cup_rows.append(build_cup_event_features(event, home_prior, away_prior, window))

    logger.info("Built %d match rows and %d cup rows", len(match_rows), len(cup_rows))
    return match_rows, cup_rows


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

    snapshots = load_season_stat_snapshots(raw_s3)
    logger.info("Loaded %d season-stats snapshot(s)", len(snapshots))

    golfer_rows = build_golfer_dataset(storage, window, course_window, snapshots)
    _write_dataset(s3, bucket, GOLFER_FEATURES_KEY, golfer_rows, "golfer")

    round_rows = build_round_dataset(storage, window)
    _write_dataset(s3, bucket, ROUND_FEATURES_KEY, round_rows, "round")

    cutline_rows = build_cutline_dataset(storage, course_window)
    _write_dataset(s3, bucket, CUTLINE_FEATURES_KEY, cutline_rows, "cutline")

    match_rows, cup_rows = build_match_and_cup_datasets(storage, window)
    _write_dataset(s3, bucket, MATCH_FEATURES_KEY, match_rows, "match")
    _write_dataset(s3, bucket, CUP_FEATURES_KEY, cup_rows, "cup")

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    main()
