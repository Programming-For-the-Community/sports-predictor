"""
NBA historical backfill job.

Pulls team, game, and per-player box score data from ESPN's public
(unofficial) site API for a range of seasons and loads it into the raw
data lake (S3) and normalized tables (DynamoDB).

Safe to re-run at any time: a game's box score fetch is skipped if its raw
S3 object already exists, and every DynamoDB write is an upsert, so an
interrupted or repeated run just fills in whatever is missing.

Walks calendar dates within each season (ESPN's scoreboard is date-based)
from October 1 of `season - 1` through June 30 of `season`; ESPN labels a
season by its ending year. A date with no games, or a date that's still
preseason, is skipped.

Player entities are derived entirely from box scores; there is no
roster-based entity seeding.

Seasons are split into batches (default: 2 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single NBAClient / rate limiter so concurrent batches don't multiply the
request rate.

Required environment variables:
    RAW_BUCKET_NAME
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION

Optional environment variables (CLI flags take precedence):
    START_SEASON (default 2016)
    END_SEASON (default 2025)
    BATCH_SIZE (default 2)
    REQUEST_DELAY_SECONDS (default 0.3)

Usage:
    python backfill.py --start-season 2016 --end-season 2025 --batch-size 2
"""
import argparse
import concurrent.futures
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

from library.http.nba import NBAClient
import normalize
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)
logger = logging.getLogger("nba-backfill")

PRESEASON_TYPE = 1  # ESPN season.type: 1=preseason, 2=regular, 3=postseason, 5=play-in


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def season_date_range(season: int) -> list[date]:
    """Every calendar date from October 1 of `season - 1` through June 30
    of `season`, inclusive."""
    start = date(season - 1, 10, 1)
    end = date(season, 6, 30)
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def seed_teams(client: NBAClient, storage: PipelineStorage) -> None:
    """ESPN's /teams isn't season-scoped, so one global call seeds every team."""
    logger.info("Seeding team entities")
    teams_response = client.get_teams()
    storage.put_raw_json("nba/teams.json", teams_response)
    league = teams_response["sports"][0]["leagues"][0]
    for team_entry in league["teams"]:
        storage.upsert_entity(normalize.team_to_entity(team_entry["team"]))
    logger.info("Seeded %d teams", len(league["teams"]))


def process_game(client: NBAClient, storage: PipelineStorage, season: int, event_id: str) -> None:
    raw_key = f"nba/boxscore/{season}/{event_id}.json"
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


def process_date(client: NBAClient, storage: PipelineStorage, date_str: str) -> dict:
    scoreboard = client.get_scoreboard_for_date(date_str)
    events = scoreboard.get("events", [])
    if not events:
        return {"games_processed": 0, "games_failed": 0, "preseason_skipped": False, "failures": []}

    if events[0].get("season", {}).get("type") == PRESEASON_TYPE:
        logger.debug("Date %s is preseason -- skipping, not backfilled by design", date_str)
        return {"games_processed": 0, "games_failed": 0, "preseason_skipped": True, "failures": []}

    season = events[0]["season"]["year"]
    storage.put_raw_json(f"nba/scoreboard/{date_str}.json", scoreboard)

    games_processed = games_failed = 0
    failures = []
    for event in events:
        event_id = event["id"]
        try:
            storage.upsert_event(normalize.scoreboard_event_to_event_item(event))
            # Only completed games have a box score to fetch.
            if event.get("status", {}).get("type", {}).get("completed", False):
                process_game(client, storage, season, event_id)
            games_processed += 1
        except Exception as exc:  # noqa: BLE001 -- log and continue, one bad game shouldn't kill the run
            games_failed += 1
            logger.exception("Failed processing event %s (date %s)", event_id, date_str)
            failures.append({"date": date_str, "event_id": event_id, "error": str(exc)})

    return {"games_processed": games_processed, "games_failed": games_failed, "preseason_skipped": False, "failures": failures}


def process_season(client: NBAClient, storage: PipelineStorage, season: int) -> dict:
    games_processed = games_failed = 0
    dates_skipped_preseason = 0
    failures = []

    for day in season_date_range(season):
        result = process_date(client, storage, day.strftime("%Y%m%d"))
        games_processed += result["games_processed"]
        games_failed += result["games_failed"]
        dates_skipped_preseason += 1 if result["preseason_skipped"] else 0
        failures.extend(result["failures"])

    return {
        "season": season,
        "games_processed": games_processed,
        "games_failed": games_failed,
        "dates_skipped_preseason": dates_skipped_preseason,
        "failures": failures,
    }


def process_batch(client: NBAClient, storage: PipelineStorage, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, storage, season)
        logger.info(
            "Finished season %s: %d games processed, %d failed, %d preseason dates skipped",
            season, result["games_processed"], result["games_failed"], result["dates_skipped_preseason"],
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical NBA data from ESPN into S3 and DynamoDB.")
    parser.add_argument("--start-season", type=int, default=int(os.environ.get("START_SEASON", 2016)))
    parser.add_argument("--end-season", type=int, default=int(os.environ.get("END_SEASON", 2025)))
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

    client = NBAClient(min_interval_seconds=args.request_delay)
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
        failure_key = f"nba/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry only the missing games",
            len(all_failures), storage.raw_bucket, failure_key,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
