"""
F1 feature engineering. Pulls every completed F1 race from DynamoDB (via
FeatureStorage), builds each driver's own rolling performance history
(plus a circuit-fit history, a constructor/car rolling-form history, and
a rolling qualifying-pace history) incrementally in a single
chronological pass, and writes THREE Parquet training datasets to S3 for
the training Fargate tasks to read: driver_features.parquet (driver-race
grain -- win/podium/DNF/finish-position/qualifying-position),
constructor_features.parquet (constructor-race grain -- constructor win
probability), and sprint_features.parquet (driver-Sprint-race grain --
Sprint win/podium/grid-position, tracked with its own entirely separate
rolling history -- see library.features.f1.build_sprint_event_features's
own docstring for why).

Genuinely simpler than every head-to-head sport's own build_dataset.py,
same reason library/features/pga.py's own module docstring gives: a
driver's own past results already live directly in events.participants
(design/DATA_SCHEMA.md) -- no player_game_stats table to pull from.
Genuinely different from PGA's own build_dataset.py in one way: a real
constructor dimension (build_constructor_dataset below), which has no
golfer-sport analog at all.

Not scheduled -- run manually via `aws ecs run-task`. Safe to re-run at
any time: it always rebuilds every dataset from the current DynamoDB
contents and overwrites the same S3 keys. sprint_features.parquet is the
one exception -- skipped (not written, not an error) whenever there's no
Sprint race data at all yet, rather than failing the whole run.

Required environment variables:
    EVENTS_TABLE_NAME
    AWS_REGION
    MODEL_ARTIFACTS_BUCKET_NAME

Optional environment variables:
    ROLLING_WINDOW (default 5) -- races of history each rolling average
    covers, see library.features.f1.DEFAULT_ROLLING_WINDOW.
    CIRCUIT_HISTORY_WINDOW (default 5) -- past appearances AT THE SAME
    CIRCUIT each circuit-fit rolling average covers, see
    library.features.f1.DEFAULT_CIRCUIT_HISTORY_WINDOW.
    TRAINING_LOOKBACK_SEASONS -- caps how far back FeatureStorage reads,
    via a since_date computed as roughly that many seasons before today.
    Unset (the default) reads full history, same as before this existed.

Usage:
    python build_dataset.py
"""
import io
import logging
import os
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from library.aws.s3_manager import S3Manager
from library.features.f1 import (
    DEFAULT_CIRCUIT_HISTORY_WINDOW,
    build_constructor_event_features,
    build_driver_event_features,
    build_sprint_event_features,
)
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-feature-engineering")

SPORT = "f1"
DRIVER_FEATURES_KEY = "f1/training-data/driver_features.parquet"
CONSTRUCTOR_FEATURES_KEY = "f1/training-data/constructor_features.parquet"
SPRINT_FEATURES_KEY = "f1/training-data/sprint_features.parquet"


def _completed_events_ascending(storage: FeatureStorage, event_type: str, since_date: str | None = None) -> list[dict]:
    # Filtered by event_type -- "field" (the main race) and "sprint" both
    # come back from the same get_all_events(SPORT) call (library/
    # normalize/f1.py writes both under this one sport), so every caller
    # here needs to pick the grain it actually wants rather than
    # accidentally mixing a Sprint race's own participants (no
    # circuit-fit/qualifying data at all) into the main race's dataset
    # or vice versa.
    events = [e for e in storage.get_all_events(SPORT, since_date=since_date) if e.get("event_type") == event_type]
    return sorted(events, key=lambda e: e.get("event_date", ""))


def build_driver_dataset(
    storage: FeatureStorage, window: int, circuit_window: int = DEFAULT_CIRCUIT_HISTORY_WINDOW,
    since_date: str | None = None,
) -> list[dict]:
    """Walks completed races in chronological order, growing each
    driver's own result history one race at a time, capped to the last
    `window` starts -- plus a SEPARATE circuit-fit history keyed by
    (driver, circuit_id), a SEPARATE constructor/car history keyed by
    constructor_id (pooled across both of that constructor's drivers --
    see library.features.f1.rolling_constructor_averages' own docstring
    for why this is a pooled history, not a per-driver one), and a
    rolling QUALIFYING-pace history (both a per-driver one and a
    constructor-pooled one, same pooling idea as the race-day
    constructor history).

    Every participant's row is built from the SAME snapshot of history --
    a race's own field never sees any other driver's result from that
    same race, only strictly earlier ones -- so this race's results are
    only folded into every history (driver, circuit, constructor,
    qualifying) after every row for this race has already been built
    (the two loops below), not interleaved participant-by-participant.
    Same discipline library/features/pga.py's own build_golfer_dataset
    uses.

    A circuit_id-less or constructor_entity_id-less participant simply
    isn't folded into that history and gets no circuit-fit/constructor-
    form signal of its own (None) -- it still gets full overall-history
    treatment. A participant whose qualifying data hasn't been merged in
    yet (result["qualifying"] is None -- see library/normalize/f1.py's
    merge_qualifying_into_event) is skipped from the qualifying history
    entirely rather than folding in a None row, same "don't pollute the
    history with a placeholder" discipline every other history here
    already follows implicitly via its own None-safe rolling functions."""
    events_ascending = _completed_events_ascending(storage, "field", since_date=since_date)
    logger.info("Loaded %d completed F1 races", len(events_ascending))

    history: dict[str, list[dict]] = defaultdict(list)  # entity_id -> ascending result dicts
    circuit_history: dict[tuple[str, str], list[dict]] = defaultdict(list)  # (entity_id, circuit_id) -> ascending result dicts
    constructor_history: dict[str, list[dict]] = defaultdict(list)  # constructor_entity_id -> pooled ascending result dicts
    qualifying_history: dict[str, list[dict]] = defaultdict(list)  # entity_id -> ascending qualifying dicts
    constructor_qualifying_history: dict[str, list[dict]] = defaultdict(list)  # constructor_entity_id -> pooled ascending qualifying dicts
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        circuit_id = event.get("circuit_id")
        participants = event.get("participants", [])
        for participant in participants:
            entity_id = participant["entity_id"]
            constructor_id = participant.get("constructor_entity_id")
            prior_results = history[entity_id][-window:][::-1]
            circuit_results = (
                circuit_history[(entity_id, circuit_id)][-circuit_window:][::-1] if circuit_id is not None else None
            )
            constructor_results = (
                constructor_history[constructor_id][-window:][::-1] if constructor_id is not None else None
            )
            prior_qualifying = qualifying_history[entity_id][-window:][::-1]
            constructor_qualifying = (
                constructor_qualifying_history[constructor_id][-window:][::-1] if constructor_id is not None else None
            )
            rows.append(build_driver_event_features(
                event, participant, prior_results, window, circuit_results, circuit_window,
                constructor_results, window, prior_qualifying, constructor_qualifying,
            ))

        for participant in participants:
            entity_id = participant["entity_id"]
            constructor_id = participant.get("constructor_entity_id")
            result = participant.get("result") or {}
            history[entity_id].append(result)
            if circuit_id is not None:
                circuit_history[(entity_id, circuit_id)].append(result)
            if constructor_id is not None:
                constructor_history[constructor_id].append(result)
            qualifying = result.get("qualifying")
            if qualifying is not None:
                qualifying_history[entity_id].append(qualifying)
                if constructor_id is not None:
                    constructor_qualifying_history[constructor_id].append(qualifying)

        if i % 50 == 0 or i == total:
            logger.info("Built driver features: %d/%d races", i, total)

    return rows


def build_constructor_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Constructor-race grain -- one row per constructor per race,
    grouping that race's participants by constructor_entity_id (1 or 2
    drivers). Tracks its own per-DRIVER history (not the pooled
    constructor history build_driver_dataset builds) since
    build_constructor_event_features needs each individual driver's own
    prior results to sum (see that function's own docstring for why sum,
    not average)."""
    events_ascending = _completed_events_ascending(storage, "field", since_date=since_date)
    logger.info("Loaded %d completed F1 races for constructor features", len(events_ascending))

    history: dict[str, list[dict]] = defaultdict(list)  # entity_id (driver) -> ascending result dicts
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        participants = event.get("participants", [])
        by_constructor: dict[str, list[dict]] = defaultdict(list)
        for participant in participants:
            constructor_id = participant.get("constructor_entity_id")
            if constructor_id is not None:
                by_constructor[constructor_id].append(participant)

        for constructor_id, constructor_participants in by_constructor.items():
            prior_by_driver = {
                p["entity_id"]: history[p["entity_id"]][-window:][::-1] for p in constructor_participants
            }
            rows.append(build_constructor_event_features(event, constructor_id, constructor_participants, prior_by_driver, window))

        for participant in participants:
            entity_id = participant["entity_id"]
            result = participant.get("result") or {}
            history[entity_id].append(result)

        if i % 50 == 0 or i == total:
            logger.info("Built constructor features: %d/%d races", i, total)

    return rows


def build_sprint_dataset(storage: FeatureStorage, window: int, since_date: str | None = None) -> list[dict]:
    """Sprint-race grain -- tracked with its OWN rolling history,
    entirely separate from build_driver_dataset's main-race history (see
    library/normalize/f1.py's sprint_result_to_event_item docstring for
    why). No circuit-fit or qualifying-pace history -- see
    library.features.f1.build_sprint_event_features's own docstring for
    why neither exists for a Sprint race."""
    events_ascending = _completed_events_ascending(storage, "sprint", since_date=since_date)
    logger.info("Loaded %d completed F1 Sprint races", len(events_ascending))

    history: dict[str, list[dict]] = defaultdict(list)  # entity_id -> ascending result dicts
    constructor_history: dict[str, list[dict]] = defaultdict(list)  # constructor_entity_id -> pooled ascending result dicts
    total = len(events_ascending)
    rows = []
    for i, event in enumerate(events_ascending, start=1):
        participants = event.get("participants", [])
        for participant in participants:
            entity_id = participant["entity_id"]
            constructor_id = participant.get("constructor_entity_id")
            prior_results = history[entity_id][-window:][::-1]
            constructor_results = constructor_history[constructor_id][-window:][::-1] if constructor_id is not None else None
            rows.append(build_sprint_event_features(event, participant, prior_results, window, constructor_results, window))

        for participant in participants:
            entity_id = participant["entity_id"]
            constructor_id = participant.get("constructor_entity_id")
            result = participant.get("result") or {}
            history[entity_id].append(result)
            if constructor_id is not None:
                constructor_history[constructor_id].append(result)

        if i % 25 == 0 or i == total:
            logger.info("Built sprint features: %d/%d races", i, total)

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
    circuit_window = int(os.environ.get("CIRCUIT_HISTORY_WINDOW", DEFAULT_CIRCUIT_HISTORY_WINDOW))
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    since_date = _lookback_since_date()

    logger.info(
        "Starting F1 feature engineering (rolling window=%d races, circuit history window=%d appearances, "
        "since_date=%s)", window, circuit_window, since_date or "unbounded",
    )

    storage = FeatureStorage()
    s3 = S3Manager(bucket, region=region)

    driver_rows = build_driver_dataset(storage, window, circuit_window, since_date=since_date)
    _write_dataset(s3, bucket, DRIVER_FEATURES_KEY, driver_rows, "driver")

    constructor_rows = build_constructor_dataset(storage, window, since_date=since_date)
    _write_dataset(s3, bucket, CONSTRUCTOR_FEATURES_KEY, constructor_rows, "constructor")

    sprint_rows = build_sprint_dataset(storage, window, since_date=since_date)
    if sprint_rows:
        _write_dataset(s3, bucket, SPRINT_FEATURES_KEY, sprint_rows, "sprint")
    else:
        # Real, expected early on -- Sprint format only exists 2021+, and
        # even within that window most rounds AREN'T Sprint weekends (a
        # handful per season). Skipped rather than raised: the driver/
        # constructor datasets above are still good and already written.
        logger.info("No Sprint race data yet -- skipping sprint_features.parquet")

    logger.info("Feature engineering complete.")


if __name__ == "__main__":
    main()
