"""
F1 historical backfill job.

Pulls race (event) and per-driver result data from Jolpica-F1 for a range
of seasons and loads it into the raw data lake (S3) and normalized tables
(DynamoDB). There is no team/roster seeding step and no player_game_stats/
team_game_stats writes -- a field-event sport's entities and results both
come from the same race-results fetch (see library/normalize/f1.py's own
docstring), and there's no per-driver box score table for field events at
all (design/DATA_SCHEMA.md).

Season depth: START_SEASON defaults to 2010, not just "the usual ~10
years" -- 2010 is when F1's CURRENT points table (25-18-15-12-10-8-6-4-
2-1) took effect; every earlier season used a different, since-retired
scoring system, and library/features/f1_points.py only implements the
current table. Backfilling further back than 2010 (Jolpica's own real
data goes back to 1950) would silently apply the wrong points to real
historical results -- 2010-present is already a 16+ season window, well
past this project's usual ~10-year backfill convention, without that
inaccuracy. Override START_SEASON if a pre-2010 backfill is ever wanted,
but library/features/f1_points.py would need a season-aware points table
first (mirrors library/features/pga_fedex_cup_points.py's own
EVENT_TIER_OVERRIDES precedent for season-scoped rule changes).

Unlike PGA's backfill (one scoreboard call per season resolves the whole
tournament list), F1's own season schedule call (JolpicaClient.get_races)
plays the identical role -- one call per season resolves every round's
number and date, then each round's own results/qualifying/pitstops/
sprint are fetched individually. Qualifying is fetched and merged into
the SAME event item results produces (library/normalize/f1.py's
merge_qualifying_into_event) before the event is ever written -- backfill
runs synchronously, so unlike the live ingest/normalize pipeline (which
has to combine two separately S3-triggered Lambda invocations) it never
needs a "write now, merge again later" two-pass dance. Sprint is written
as its own real, separately normalized event (event_type "sprint") when
the round actually was a Sprint weekend, not raw-only. Pitstops stays
raw-only -- nothing normalizes it into a feature-ready shape yet.

Safe to re-run at any time: a round's raw JSON FETCH is skipped per-file
if its S3 object already exists (reused from S3 instead), but it's still
re-run through this script's CURRENT normalize logic every time
regardless of whether it was already cached -- same reasoning
data-backfills/pga/backfill.py's own process_tournament docstring gives.
Every DynamoDB write is an upsert, so an interrupted or repeated run just
fills in whatever is missing.

An empty response (a round/qualifying session that genuinely hasn't run
yet -- always a real, not-a-permanent-gap case: Jolpica's own schedule
never lists a canceled/never-run round at all, e.g. the 2020 Australian
GP and 2022 Russian GP cancellations are both fully absent from their
season's own schedule rather than appearing as a phantom entry) is
deliberately never cached to S3 -- see process_round
and _fetch_qualifying's own docstrings. Caching an empty response would
make the "skip the fetch if already cached" optimization above
permanently trust a stale "not run yet" snapshot even after the real
data becomes available, silently freezing that round out of every
future re-run's results.

Seasons are split into batches (default: 3 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single JolpicaClient / rate limiter so concurrent batches don't multiply
the request rate against Jolpica's own strict sustained-rate limit (see
library/http/f1.py's own docstring) -- concurrency here mainly overlaps
per-round normalize/DynamoDB work with the next request's rate-limit
wait, not a way to exceed the shared request budget.

Required environment variables:
    RAW_BUCKET_NAME
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    AWS_REGION

Optional environment variables (CLI flags take precedence):
    START_SEASON (default 2010)
    END_SEASON (default 2026)
    BATCH_SIZE (default 3)
    REQUEST_DELAY_SECONDS (default library.http.f1's own sustained-rate bound)

Usage:
    python backfill.py --start-season 2010 --end-season 2026 --batch-size 3
"""
import argparse
import concurrent.futures
import logging
import os
import sys
import time
from datetime import datetime, timezone

import normalize
from library.http.f1 import DEFAULT_MIN_INTERVAL_SECONDS, JolpicaClient
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)
logger = logging.getLogger("f1-backfill")

# Jolpica's own docs state pitstops data starts from the 2011 season --
# see library/http/f1.py's get_pitstops docstring.
PITSTOPS_MIN_SEASON = 2011


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def _fetch_qualifying(client: JolpicaClient, storage: PipelineStorage, season: int, round_: int) -> dict | None:
    """Fetched/cached BEFORE the event item is built (unlike the live
    ingest/normalize pipeline, which has to combine two separately
    S3-triggered Lambda invocations -- see aws-lambdas/f1/normalize/
    handler.py's own docstring) -- backfill runs synchronously, so it
    can simply merge qualifying in a single pass. None on any failure
    (a request error, or a genuinely not-yet-run qualifying session) --
    the round is still written without it rather than blocked.

    Only caches a REAL response (a real QualifyingResults list) -- an
    empty one (this round's qualifying session hasn't happened yet, e.g.
    a currently in-progress season) is returned but NOT written to S3.
    Caching an empty response here would permanently freeze this round's
    qualifying as "never available": a later re-run's own storage.
    raw_object_exists check would find that stale empty file and trust
    it forever, never re-querying Jolpica even after the session
    actually happens and real data exists. Confirmed this isn't a
    theoretical concern -- Jolpica's own schedule never lists a
    canceled/never-run round at all (verified live against the real
    2020 Australian GP and 2022 Russian GP cancellations, both fully
    absent from their season's schedule), so every round this function
    is ever called for WILL eventually have real qualifying data; the
    only question is whether it exists YET."""
    try:
        qualifying_key = f"f1/qualifying/{season}/{round_}.json"
        if storage.raw_object_exists(qualifying_key):
            return storage.get_raw_json(qualifying_key)
        qualifying = client.get_qualifying(season, round_)
        races = qualifying.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if races and races[0].get("QualifyingResults"):
            storage.put_raw_json(qualifying_key, qualifying)
        else:
            logger.info("Season %d round %d has no qualifying data yet -- not cached, will re-check next run", season, round_)
        return qualifying
    except Exception:
        logger.exception("Failed fetching qualifying for season %d round %d -- event written without it", season, round_)
        return None


def _fetch_and_process_sprint(client: JolpicaClient, storage: PipelineStorage, season: int, round_: int) -> None:
    """Sprint is a real, separately normalized event (event_type
    "sprint") now -- see library/normalize/f1.py's sprint_result_to_
    event_item docstring -- not raw-only. A failure here is logged but
    doesn't fail the round; the main race result is already safely
    written by the caller before this is called."""
    try:
        sprint_key = f"f1/sprint/{season}/{round_}.json"
        if storage.raw_object_exists(sprint_key):
            sprint = storage.get_raw_json(sprint_key)
        else:
            sprint = client.get_sprint(season, round_)
            sprint_races = sprint.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            if not (sprint_races and sprint_races[0].get("SprintResults")):
                return  # not a real Sprint weekend -- nothing to cache or normalize
            storage.put_raw_json(sprint_key, sprint)

        sprint_races = sprint.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        if not (sprint_races and sprint_races[0].get("SprintResults")):
            return  # a cached-but-empty file from before this round was confirmed a Sprint

        for entity in normalize.sprint_result_to_constructor_entities(sprint):
            storage.upsert_entity(entity)
        for entity in normalize.sprint_result_to_driver_entities(sprint):
            storage.upsert_entity(entity)
        storage.upsert_event(normalize.sprint_result_to_event_item(sprint))
    except Exception:
        logger.exception("Failed fetching/processing sprint for season %d round %d", season, round_)


def _cache_pitstops(client: JolpicaClient, storage: PipelineStorage, season: int, round_: int) -> None:
    """Still raw-only (see aws-lambdas/f1/normalize/handler.py's own
    docstring) -- nothing normalizes pitstop data into a feature-ready
    shape yet."""
    try:
        pitstops_key = f"f1/pitstops/{season}/{round_}.json"
        if not storage.raw_object_exists(pitstops_key):
            storage.put_raw_json(pitstops_key, client.get_pitstops(season, round_))
    except Exception:
        logger.exception("Failed fetching pitstops for season %d round %d", season, round_)


def process_round(client: JolpicaClient, storage: PipelineStorage, season: int, round_: int) -> str:
    """Returns "processed" (written to DynamoDB) or "skipped" (round
    hasn't been run yet -- a real, expected case for the season currently
    in progress, not an error). process_season still wraps this call in
    a try/except -- only a genuine unexpected failure should reach that.

    The raw results FETCH is only cached to S3 once it's confirmed real
    (a non-empty Results list) -- caching an empty "not run yet" response
    under the same raw_key a real result will eventually occupy would
    permanently freeze this round as "skipped" forever: a later re-run's
    own storage.raw_object_exists check would find that stale empty file
    and trust it, never re-querying Jolpica even after the race actually
    happens. Same fix, same reasoning, as _fetch_qualifying's own
    docstring above."""
    raw_key = f"f1/results/{season}/{round_}.json"
    was_cached = storage.raw_object_exists(raw_key)
    results = storage.get_raw_json(raw_key) if was_cached else client.get_race_results(season, round_)

    races = results.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races or not races[0].get("Results"):
        logger.info("Season %d round %d has no results yet -- skipped (not cached, will re-check next run)", season, round_)
        return "skipped"

    if not was_cached:
        storage.put_raw_json(raw_key, results)

    # Constructors before drivers, drivers before the event that
    # references them both -- same ordering aws-lambdas/f1/normalize/
    # handler.py's own _process_results uses.
    for entity in normalize.race_result_to_constructor_entities(results):
        storage.upsert_entity(entity)
    for entity in normalize.race_result_to_driver_entities(results):
        storage.upsert_entity(entity)

    event_item = normalize.race_result_to_event_item(results)
    qualifying = _fetch_qualifying(client, storage, season, round_)
    normalize.merge_qualifying_into_event(event_item, qualifying)
    storage.upsert_event(event_item)

    _fetch_and_process_sprint(client, storage, season, round_)
    if season >= PITSTOPS_MIN_SEASON:
        _cache_pitstops(client, storage, season, round_)

    return "processed"


def process_season(client: JolpicaClient, storage: PipelineStorage, season: int) -> dict:
    schedule = client.get_races(season)
    storage.put_raw_json(f"f1/schedule/{season}.json", schedule)
    races = schedule.get("MRData", {}).get("RaceTable", {}).get("Races", [])

    rounds_processed = rounds_skipped = rounds_failed = 0
    failures = []
    for race in races:
        round_ = int(race["round"])
        try:
            result = process_round(client, storage, season, round_)
            if result == "processed":
                rounds_processed += 1
            else:
                rounds_skipped += 1
        except Exception as exc:  # noqa: BLE001 -- log and continue, one bad round shouldn't kill the run
            rounds_failed += 1
            logger.exception("Failed processing season %d round %d", season, round_)
            failures.append({"season": season, "round": round_, "error": str(exc)})

    return {
        "season": season,
        "rounds_processed": rounds_processed,
        "rounds_skipped": rounds_skipped,
        "rounds_failed": rounds_failed,
        "failures": failures,
    }


def process_batch(client: JolpicaClient, storage: PipelineStorage, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, storage, season)
        logger.info(
            "Finished season %s: %d round(s) processed, %d skipped (not yet run), %d failed",
            season, result["rounds_processed"], result["rounds_skipped"], result["rounds_failed"],
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical F1 data from Jolpica-F1 into S3 and DynamoDB.")
    parser.add_argument("--start-season", type=int, default=int(os.environ.get("START_SEASON", 2010)))
    parser.add_argument("--end-season", type=int, default=int(os.environ.get("END_SEASON", 2026)))
    parser.add_argument(
        "--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 3)),
        help="Seasons per concurrent worker",
    )
    parser.add_argument(
        "--request-delay", type=float, default=float(os.environ.get("REQUEST_DELAY_SECONDS", DEFAULT_MIN_INTERVAL_SECONDS)),
        help="Minimum seconds between any two Jolpica requests, enforced across all workers combined",
    )
    args = parser.parse_args()

    batches = chunk_seasons(args.start_season, args.end_season, args.batch_size)
    logger.info(
        "Running backfill for seasons %d-%d in %d batch(es) of up to %d season(s) each",
        args.start_season, args.end_season, len(batches), args.batch_size,
    )

    client = JolpicaClient(min_interval_seconds=args.request_delay)
    storage = PipelineStorage()

    all_results = []
    start_time = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches), thread_name_prefix="batch") as executor:
        futures = {executor.submit(process_batch, client, storage, batch): batch for batch in batches}
        for future in concurrent.futures.as_completed(futures):
            batch = futures[future]
            try:
                all_results.extend(future.result())
            except Exception:  # noqa: BLE001 -- a whole batch dying shouldn't stop us reporting the others
                logger.exception("Batch %s raised an unhandled exception", batch)

    elapsed = time.monotonic() - start_time
    total_processed = sum(r["rounds_processed"] for r in all_results)
    total_skipped = sum(r["rounds_skipped"] for r in all_results)
    total_failed = sum(r["rounds_failed"] for r in all_results)
    all_failures = [failure for r in all_results for failure in r["failures"]]

    logger.info(
        "Backfill complete in %.1fs: %d round(s) processed, %d skipped (not yet run), %d failed",
        elapsed, total_processed, total_skipped, total_failed,
    )

    if all_failures:
        failure_key = f"f1/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry only the missing rounds",
            len(all_failures), storage.raw_bucket, failure_key,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
