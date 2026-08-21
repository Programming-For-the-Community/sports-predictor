"""
NCAA MBB historical backfill job.

Pulls team, game, and per-player box score data from ESPN's public
(unofficial) site API for a range of seasons and loads it into the raw
data lake (S3) and normalized tables (DynamoDB).

Safe to re-run at any time: a game's box score fetch is skipped if its raw
S3 object already exists, and every DynamoDB write is an upsert, so an
interrupted or repeated run just fills in whatever is missing.

Walks calendar dates within each season (ESPN's scoreboard is date-based)
from November 1 of `season - 1` through May 15 of `season`; ESPN labels a
season by its ending year (confirmed live, 2026-08-20: a 2025-11-03 game
carries season.year=2026), same convention as NBA's. Unlike NBA/NFL, there
is no preseason concept to skip here -- confirmed live, 2026-08-19 (see
aws-lambdas/ncaambb/ingest/handler.py's own docstring): ESPN's NCAA MBB
scoreboard has zero events before the real regular-season start date, and
the earliest games of a season already carry season.type=2/"regular-
season". May 15 pads about 5 weeks past the early-April National
Championship, same padding spirit as NBA's own end-of-season buffer.

Player entities are derived entirely from box scores; there is no
roster-based entity seeding.

Seasons are split into batches (default: 2 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single NCAAMBBClient / rate limiter so concurrent batches don't multiply
the request rate.

VOLUME: unlike NBA (~30 teams, ~15 games on a busy night), D1 has ~362
teams and a single busy Saturday can carry ~150-155 games (confirmed live,
2026-08-19 -- see project-ncaambb-onboarding memory), which multiplies
into roughly 4x NBA's total games per season. process_date's own
per-event box-score fetch loop uses a ThreadPoolExecutor of
_DATE_MAX_WORKERS workers (same shared-limiter idiom as the season-batch
concurrency above, and as aws-lambdas/ncaambb/ingest/handler.py's own
fetch loops) so a single date with a full slate of games doesn't
serialize each game's full request-plus-network-latency cost within one
season-batch thread. This does not increase the request rate against
ESPN -- the shared RateLimiter still caps that regardless of how many
threads are waiting on it.

Required environment variables:
    RAW_BUCKET_NAME
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION

Optional environment variables (CLI flags take precedence):
    START_SEASON (default 2016)
    END_SEASON (default 2026)
    BATCH_SIZE (default 2)
    REQUEST_DELAY_SECONDS (default 0.3)

Usage:
    python backfill.py --start-season 2016 --end-season 2026 --batch-size 2
"""
import argparse
import concurrent.futures
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

from library.http.ncaambb import NCAAMBBClient
import normalize
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)
logger = logging.getLogger("ncaambb-backfill")

# I/O-bound HTTP calls sharing one rate-limited client -- see this
# module's own VOLUME docstring section for why this exists at all (NBA's
# equivalent loop stays sequential; its volume never justified this).
_DATE_MAX_WORKERS = 8


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def season_date_range(season: int) -> list[date]:
    """Every calendar date from November 1 of `season - 1` through May 15
    of `season`, inclusive."""
    start = date(season - 1, 11, 1)
    end = date(season, 5, 15)
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def seed_teams(client: NCAAMBBClient, storage: PipelineStorage) -> None:
    """ESPN's /teams isn't season-scoped, so one global call seeds every team."""
    logger.info("Seeding team entities")
    teams_response = client.get_teams()
    storage.put_raw_json("ncaambb/teams.json", teams_response)
    league = teams_response["sports"][0]["leagues"][0]
    for team_entry in league["teams"]:
        storage.upsert_entity(normalize.team_to_entity(team_entry["team"]))
    logger.info("Seeded %d teams", len(league["teams"]))


def process_game(client: NCAAMBBClient, storage: PipelineStorage, season: int, event_id: str) -> None:
    raw_key = f"ncaambb/boxscore/{season}/{event_id}.json"
    if storage.raw_object_exists(raw_key):
        logger.debug("Box score already loaded, skipping event %s", event_id)
        return
    summary = client.get_summary(event_id)
    storage.put_raw_json(raw_key, summary)
    stats_items, player_entities = normalize.boxscore_to_player_game_stats(summary)
    for entity in player_entities:
        storage.upsert_player_entity(entity)
    storage.write_player_game_stats(stats_items)
    storage.write_team_game_stats(normalize.boxscore_to_team_game_stats(summary))


def _process_one_event(client: NCAAMBBClient, storage: PipelineStorage, season: int, event: dict) -> None:
    """One event's full processing (event upsert + box score, if
    completed) -- runs on a worker thread, see process_date below."""
    event_id = event["id"]
    storage.upsert_event(normalize.scoreboard_event_to_event_item(event))
    # Only completed games have a box score to fetch.
    if event.get("status", {}).get("type", {}).get("completed", False):
        process_game(client, storage, season, event_id)


def process_date(client: NCAAMBBClient, storage: PipelineStorage, date_str: str) -> dict:
    scoreboard = client.get_scoreboard_for_date(date_str)
    events = scoreboard.get("events", [])
    if not events:
        return {"games_processed": 0, "games_failed": 0, "failures": []}

    season = events[0]["season"]["year"]
    storage.put_raw_json(f"ncaambb/scoreboard/{date_str}.json", scoreboard)

    games_processed = games_failed = 0
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_DATE_MAX_WORKERS, thread_name_prefix="date") as executor:
        futures = {executor.submit(_process_one_event, client, storage, season, event): event for event in events}
        for future in concurrent.futures.as_completed(futures):
            event = futures[future]
            try:
                future.result()
                games_processed += 1
            except Exception as exc:  # noqa: BLE001 -- log and continue, one bad game shouldn't kill the run
                games_failed += 1
                logger.exception("Failed processing event %s (date %s)", event["id"], date_str)
                failures.append({"date": date_str, "event_id": event["id"], "error": str(exc)})

    return {"games_processed": games_processed, "games_failed": games_failed, "failures": failures}


def process_season(client: NCAAMBBClient, storage: PipelineStorage, season: int) -> dict:
    games_processed = games_failed = 0
    failures = []

    for day in season_date_range(season):
        result = process_date(client, storage, day.strftime("%Y%m%d"))
        games_processed += result["games_processed"]
        games_failed += result["games_failed"]
        failures.extend(result["failures"])

    return {
        "season": season,
        "games_processed": games_processed,
        "games_failed": games_failed,
        "failures": failures,
    }


def process_batch(client: NCAAMBBClient, storage: PipelineStorage, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, storage, season)
        logger.info(
            "Finished season %s: %d games processed, %d failed",
            season, result["games_processed"], result["games_failed"],
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical NCAA MBB data from ESPN into S3 and DynamoDB.")
    parser.add_argument("--start-season", type=int, default=int(os.environ.get("START_SEASON", 2016)))
    parser.add_argument("--end-season", type=int, default=int(os.environ.get("END_SEASON", 2026)))
    parser.add_argument(
        "--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 2)),
        help="Seasons per concurrent worker",
    )
    parser.add_argument(
        "--request-delay", type=float, default=float(os.environ.get("REQUEST_DELAY_SECONDS", 0.3)),
        help="Minimum seconds between any two ESPN requests, enforced across all workers combined",
    )
    args = parser.parse_args()

    batches = chunk_seasons(args.start_season, args.end_season, args.batch_size)
    logger.info(
        "Running backfill for seasons %d-%d in %d batch(es) of up to %d season(s) each",
        args.start_season, args.end_season, len(batches), args.batch_size,
    )

    client = NCAAMBBClient(min_interval_seconds=args.request_delay)
    storage = PipelineStorage()
    seed_teams(client, storage)

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
    total_games = sum(r["games_processed"] for r in all_results)
    total_failed = sum(r["games_failed"] for r in all_results)
    all_failures = [failure for r in all_results for failure in r["failures"]]

    logger.info("Backfill complete in %.1fs: %d games processed, %d failed", elapsed, total_games, total_failed)

    if all_failures:
        failure_key = f"ncaambb/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry only the missing games",
            len(all_failures), storage.raw_bucket, failure_key,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
