"""
NFL historical backfill job.

Pulls team, game, and per-player box score data from ESPN's public
(unofficial) site API for a range of seasons and loads it into the raw
data lake (S3) and normalized tables (DynamoDB).

Safe to re-run at any time: a game's box score fetch is skipped if its raw
S3 object already exists, and every DynamoDB write is an upsert (put_item
on the same key), so an interrupted or repeated run just fills in whatever
is missing rather than duplicating or erroring.

Seasons are split into batches (default: 2 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single EspnClient / rate limiter so N concurrent batches don't multiply
the request rate by N against a host we don't control.

Required environment variables:
    RAW_BUCKET_NAME
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    AWS_REGION

Optional environment variables -- these exist so an ECS "Run Task" console
launch can override just the season range for that one run (via container
overrides) without editing the task definition. CLI flags, when passed,
take precedence over both.
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
from datetime import datetime, timezone

import espn_client
import normalize
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("nfl-backfill")

# Looping through week 18 is harmless for pre-2021 seasons (only 17 weeks
# existed then) -- ESPN just returns an empty events list for that week.
REGULAR_SEASON_WEEKS = range(1, 19)
POSTSEASON_WEEKS = range(1, 6)  # wildcard through Super Bowl
SEASON_TYPES = {"regular": 2, "postseason": 3}


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def seed_teams(client: espn_client.EspnClient) -> None:
    logger.info("Seeding team entities")
    teams_response = client.get_teams()
    storage.put_raw_json("nfl/teams.json", teams_response)
    league = teams_response["sports"][0]["leagues"][0]
    for team_entry in league["teams"]:
        storage.upsert_entity(normalize.team_to_entity(team_entry["team"]))
    logger.info("Seeded %d teams", len(league["teams"]))


def process_game(client: espn_client.EspnClient, season: int, event_id: str) -> None:
    raw_key = f"nfl/boxscore/{season}/{event_id}.json"
    if storage.raw_object_exists(raw_key):
        logger.debug("Box score already loaded, skipping event %s", event_id)
        return
    summary = client.get_summary(event_id)
    storage.put_raw_json(raw_key, summary)
    stats_items, player_entities = normalize.boxscore_to_player_game_stats(summary)
    for entity in player_entities:
        storage.upsert_entity(entity)
    storage.write_player_game_stats(stats_items)


def process_season(client: espn_client.EspnClient, season: int) -> dict:
    games_processed = 0
    games_failed = 0
    failures = []

    for label, seasontype in SEASON_TYPES.items():
        weeks = REGULAR_SEASON_WEEKS if label == "regular" else POSTSEASON_WEEKS
        for week in weeks:
            scoreboard = client.get_scoreboard(season, seasontype, week)
            storage.put_raw_json(f"nfl/scoreboard/{season}/{seasontype}/{week}.json", scoreboard)
            events = scoreboard.get("events", [])
            for event in events:
                event_id = event["id"]
                try:
                    storage.upsert_event(normalize.scoreboard_event_to_event_item(event))
                    process_game(client, season, event_id)
                    games_processed += 1
                except Exception as exc:  # noqa: BLE001 -- log and continue, one bad game shouldn't kill the run
                    games_failed += 1
                    logger.exception("Failed processing event %s (season %s)", event_id, season)
                    failures.append({"season": season, "event_id": event_id, "error": str(exc)})

    return {
        "season": season,
        "games_processed": games_processed,
        "games_failed": games_failed,
        "failures": failures,
    }


def process_batch(client: espn_client.EspnClient, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, season)
        logger.info(
            "Finished season %s: %d games processed, %d failed",
            season, result["games_processed"], result["games_failed"],
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical NFL data from ESPN into S3 and DynamoDB.")
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

    client = espn_client.EspnClient(min_interval_seconds=args.request_delay)
    seed_teams(client)

    all_results = []
    start_time = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches), thread_name_prefix="batch") as executor:
        futures = {executor.submit(process_batch, client, batch): batch for batch in batches}
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
        failure_key = f"nfl/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry only the missing games",
            len(all_failures), storage.RAW_BUCKET, failure_key,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
