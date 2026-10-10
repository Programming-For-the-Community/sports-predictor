"""
NHL historical backfill job.

Pulls team, game, and per-player box score data from ESPN's public
(unofficial) site API for a range of seasons and loads it into the raw
data lake (S3) and normalized tables (DynamoDB). Each game's raw summary
also carries its full play-by-play.

Safe to re-run at any time: a game's box score fetch is skipped if its raw
S3 object already exists, and every DynamoDB write is an upsert, so an
interrupted or repeated run just fills in whatever is missing.

Walks calendar dates within each season (ESPN's scoreboard is date-based)
from October 1 of `season - 1` through September 30 of `season`; ESPN
labels a season by its ending year. The window runs through September
because the 2020 playoffs finished on September 28 and the 2021 season
ran January to July. Preseason, All-Star and international-tournament
games are skipped (library.normalize.nhl.is_ingestable_event).

Player entities are derived entirely from box scores; there is no
roster-based entity seeding.

Seasons are split into batches (default: 2 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single NHLClient / rate limiter so concurrent batches don't multiply the
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
    END_SEASON (default 2027)
    BATCH_SIZE (default 2)
    REQUEST_DELAY_SECONDS (default 0.3)

Usage:
    python backfill.py --start-season 2016 --end-season 2027 --batch-size 2
"""
import logging
from datetime import date, timedelta

from library.data_backfills import espn_backfill_common
from library.http.nhl import NHLClient
from library.normalize.nhl import is_ingestable_event
import normalize
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)
logger = logging.getLogger("nhl-backfill")

SPORT = "nhl"
DEFAULT_START_SEASON = 2016
DEFAULT_END_SEASON = 2027


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def season_date_range(season: int, today: date | None = None) -> list[date]:
    """Every calendar date from October 1 of `season - 1` through
    September 30 of `season`, inclusive, capped at today."""
    start = date(season - 1, 10, 1)
    end = min(date(season, 9, 30), today or date.today())
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def seed_teams(client: NHLClient, storage: PipelineStorage) -> None:
    espn_backfill_common.seed_teams(client, storage, normalize, SPORT, logger)


def process_game(client: NHLClient, storage: PipelineStorage, season: int, event_id: str) -> None:
    espn_backfill_common.process_game(client, storage, normalize, SPORT, season, event_id, logger)


def process_date(client: NHLClient, storage: PipelineStorage, date_str: str) -> dict:
    scoreboard = client.get_scoreboard_for_date(date_str)
    all_events = scoreboard.get("events", [])
    events = [event for event in all_events if is_ingestable_event(event)]
    games_skipped = len(all_events) - len(events)
    if not events:
        return {"games_processed": 0, "games_failed": 0, "games_skipped": games_skipped, "failures": []}

    storage.put_raw_json(f"{SPORT}/scoreboard/{date_str}.json", scoreboard)

    games_processed = games_failed = 0
    failures = []
    for event in events:
        event_id = event["id"]
        try:
            storage.upsert_event(normalize.scoreboard_event_to_event_item(event))
            # Only completed games have a box score to fetch.
            if event.get("status", {}).get("type", {}).get("completed", False):
                process_game(client, storage, event["season"]["year"], event_id)
            games_processed += 1
        # Log and continue -- one bad game shouldn't kill the run.
        except Exception as exc:  # noqa: BLE001
            games_failed += 1
            logger.exception("Failed processing event %s (date %s)", event_id, date_str)
            failures.append({"date": date_str, "event_id": event_id, "error": str(exc)})

    return {"games_processed": games_processed, "games_failed": games_failed, "games_skipped": games_skipped, "failures": failures}


def process_season(client: NHLClient, storage: PipelineStorage, season: int) -> dict:
    games_processed = games_failed = games_skipped = 0
    failures = []

    for day in season_date_range(season):
        result = process_date(client, storage, day.strftime("%Y%m%d"))
        games_processed += result["games_processed"]
        games_failed += result["games_failed"]
        games_skipped += result["games_skipped"]
        failures.extend(result["failures"])

    return {
        "season": season,
        "games_processed": games_processed,
        "games_failed": games_failed,
        "games_skipped": games_skipped,
        "failures": failures,
    }


def process_batch(client: NHLClient, storage: PipelineStorage, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, storage, season)
        logger.info(
            "Finished season %s: %d games processed, %d failed, %d skipped (preseason/exhibition)",
            season, result["games_processed"], result["games_failed"], result["games_skipped"],
        )
        results.append(result)
    return results


def main() -> None:
    args = espn_backfill_common.parse_args(
        "Backfill historical NHL data from ESPN into S3 and DynamoDB.", DEFAULT_START_SEASON, DEFAULT_END_SEASON,
    )
    batches = chunk_seasons(args.start_season, args.end_season, args.batch_size)
    logger.info(
        "Running backfill for seasons %d-%d in %d batch(es) of up to %d season(s) each",
        args.start_season, args.end_season, len(batches), args.batch_size,
    )

    client = NHLClient(min_interval_seconds=args.request_delay)
    storage = PipelineStorage()
    seed_teams(client, storage)
    espn_backfill_common.run_batches(batches, lambda batch: process_batch(client, storage, batch), storage, SPORT, logger)


if __name__ == "__main__":
    main()
