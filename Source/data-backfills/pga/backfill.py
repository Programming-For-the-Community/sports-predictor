"""
PGA historical backfill job.

Pulls tournament (event) and per-competitor result data from ESPN's public
(unofficial) site API for a range of seasons and loads it into the raw
data lake (S3) and normalized tables (DynamoDB). There is no team/roster
seeding step and no player_game_stats/team_game_stats writes -- a field-
event sport's entities and results both come from the same leaderboard
fetch (see library/normalize/pga.py's own docstring), and there's no
per-player box score table for field events at all (design/DATA_SCHEMA.md).

Safe to re-run at any time: a tournament's raw leaderboard fetch is
skipped if its S3 object already exists, and every DynamoDB write is an
upsert, so an interrupted or repeated run just fills in whatever is
missing.

Unlike every head-to-head sport's backfill (which walks every individual
calendar date within a season, since a day-by-day schedule is the only
way to discover games), one scoreboard call per season resolves that
whole season's entire tournament list in one shot --
`response["leagues"][0]["calendar"]`, ~45-51 entries -- confirmed live,
2026-08-24 (see PGAClient.get_scoreboard_for_date's own docstring). The
`dates` param just needs to land somewhere inside the season's window;
June 1 of the season's own label year is used since ESPN labels a PGA
season by the year it ENDS (a season spanning Sept of year N-1 through
Aug of year N is labeled N) and June always falls inside that span.

Seasons are split into batches (default: 3 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single PGAClient / rate limiter so concurrent batches don't multiply the
request rate.

Required environment variables:
    RAW_BUCKET_NAME
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    AWS_REGION

Optional environment variables (CLI flags take precedence):
    START_SEASON (default 2017)
    END_SEASON (default 2026)
    BATCH_SIZE (default 3)
    REQUEST_DELAY_SECONDS (default 0.3)

Usage:
    python backfill.py --start-season 2017 --end-season 2026 --batch-size 3
"""
import argparse
import concurrent.futures
import logging
import os
import sys
import time
from datetime import datetime, timezone

from library.http.pga import PGAClient
from library.normalize.pga import is_medal_scoring
import normalize
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)
logger = logging.getLogger("pga-backfill")


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def season_calendar(client: PGAClient, season: int) -> tuple[list[dict], dict]:
    """This season's full tournament list, plus the raw scoreboard
    response the calendar came from (written to S3 for traceability, same
    "write the raw response too" convention every other sport's backfill
    follows). June 1 always falls inside a PGA season's own Sept-Aug span
    -- see this module's own docstring."""
    scoreboard = client.get_scoreboard_for_date(f"{season}0601")
    leagues = scoreboard.get("leagues") or [{}]
    return leagues[0].get("calendar") or [], scoreboard


def process_tournament(client: PGAClient, storage: PipelineStorage, season: int, event_id: str) -> str:
    """Returns "processed" (written to DynamoDB), "skipped" (a real,
    expected reason -- already loaded, empty response, or a non-stroke-
    play tournament), "empty" (a real ESPN data gap -- see below), never
    raises for any of those. process_season still wraps this call in a
    try/except -- only a genuine unexpected failure (an ESPN request
    error, a malformed payload this function doesn't already guard
    against) should ever reach that."""
    raw_key = f"pga/leaderboard/{season}/{event_id}.json"
    if storage.raw_object_exists(raw_key):
        logger.debug("Leaderboard already loaded, skipping event %s", event_id)
        return "skipped"
    leaderboard = client.get_leaderboard(event_id)
    storage.put_raw_json(raw_key, leaderboard)

    events = leaderboard.get("events", [])
    if not events:
        logger.warning("No events in leaderboard response for event %s", event_id)
        return "skipped"
    event = events[0]

    # Ryder Cup/Presidents Cup/WGC Match Play (team or individual match
    # play) and Zurich Classic of New Orleans (team stroke play) are real
    # PGA TOUR calendar entries this project's schema/normalizers don't
    # support -- see library.normalize.pga.is_medal_scoring's own
    # docstring for the confirmed-live crash this guards against. Raw
    # JSON is still written above either way (preserves the record), it
    # just never reaches DynamoDB.
    if not is_medal_scoring(event):
        logger.info(
            "Skipping event %s -- not stroke-play scoring (tournament=%r, scoringSystem=%r)",
            event_id, (event.get("tournament") or {}).get("displayName"),
            (event.get("tournament") or {}).get("scoringSystem", {}).get("name"),
        )
        return "skipped"

    # A real, confirmed ESPN gap, distinct from "not stroke play" above --
    # a Medal-scoring event whose own competition object has no
    # "competitors" key at all. Live-swept across every 2017-2025 season
    # calendar entry, 2026-08-26: most of these are genuinely-canceled
    # 2020 COVID-disruption tournaments (nothing was ever played, so
    # there's nothing to lose), but a handful of real, completed, played
    # Fall-2020 events (Shriners Hospitals for Children Open, Sanderson
    # Farms Championship, Corales Puntacana Championship, all STATUS_FINAL
    # completed=true) have this exact same gap -- ESPN itself never
    # populated competitor data for them, not a parsing issue on our end.
    # Writing this event anyway (with participants=[]) would silently
    # corrupt the cutline dataset's field_size feature (derived from
    # len(participants)) to 0 for a real ~130+ player field, so this is
    # skipped entirely rather than partially written -- the raw response
    # above still preserves whatever ESPN did return, in case a future
    # re-fetch (after deleting the cached S3 object) ever recovers more.
    competition = event["competitions"][0] if event.get("competitions") else {}
    if not competition.get("competitors"):
        logger.warning(
            "Event %s (%s, season %s) is Medal scoring but has no competitor data on its leaderboard "
            "response (status=%s) -- a real ESPN gap, not written to DynamoDB.",
            event_id, (event.get("tournament") or {}).get("displayName"), season,
            (event.get("status") or {}).get("type", {}).get("name"),
        )
        return "empty"

    for entity in normalize.leaderboard_event_to_player_entities(event):
        storage.upsert_entity(entity)
    storage.upsert_event(normalize.leaderboard_event_to_event_item(event))
    return "processed"


def process_season(client: PGAClient, storage: PipelineStorage, season: int) -> dict:
    calendar, scoreboard = season_calendar(client, season)
    storage.put_raw_json(f"pga/scoreboard/{season}0601.json", scoreboard)

    tournaments_processed = tournaments_skipped = tournaments_failed = tournaments_empty = 0
    empty_events = []
    failures = []
    for entry in calendar:
        event_id = entry["id"]
        try:
            result = process_tournament(client, storage, season, event_id)
            if result == "processed":
                tournaments_processed += 1
            elif result == "empty":
                tournaments_empty += 1
                empty_events.append({"season": season, "event_id": event_id, "label": entry.get("label")})
            else:
                tournaments_skipped += 1
        except Exception as exc:  # noqa: BLE001 -- log and continue, one bad tournament shouldn't kill the run
            tournaments_failed += 1
            logger.exception("Failed processing event %s (%s, season %s)", event_id, entry.get("label"), season)
            failures.append({"season": season, "event_id": event_id, "label": entry.get("label"), "error": str(exc)})

    return {
        "season": season,
        "tournaments_processed": tournaments_processed,
        "tournaments_skipped": tournaments_skipped,
        "tournaments_empty": tournaments_empty,
        "tournaments_failed": tournaments_failed,
        "empty_events": empty_events,
        "failures": failures,
    }


def process_batch(client: PGAClient, storage: PipelineStorage, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, storage, season)
        logger.info(
            "Finished season %s: %d tournaments processed, %d skipped, %d empty (ESPN gap), %d failed",
            season, result["tournaments_processed"], result["tournaments_skipped"],
            result["tournaments_empty"], result["tournaments_failed"],
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical PGA data from ESPN into S3 and DynamoDB.")
    parser.add_argument("--start-season", type=int, default=int(os.environ.get("START_SEASON", 2017)))
    parser.add_argument("--end-season", type=int, default=int(os.environ.get("END_SEASON", 2026)))
    parser.add_argument(
        "--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 3)),
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

    client = PGAClient(min_interval_seconds=args.request_delay)
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
    total_tournaments = sum(r["tournaments_processed"] for r in all_results)
    total_skipped = sum(r["tournaments_skipped"] for r in all_results)
    total_empty = sum(r["tournaments_empty"] for r in all_results)
    total_failed = sum(r["tournaments_failed"] for r in all_results)
    all_failures = [failure for r in all_results for failure in r["failures"]]
    all_empty_events = [empty for r in all_results for empty in r["empty_events"]]

    logger.info(
        "Backfill complete in %.1fs: %d tournaments processed, %d skipped, %d empty (ESPN gap), %d failed",
        elapsed, total_tournaments, total_skipped, total_empty, total_failed,
    )

    if all_empty_events:
        empty_key = f"pga/backfill-empty-events/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(empty_key, {"empty_events": all_empty_events})
        logger.warning(
            "Wrote %d tournament(s) with no ESPN competitor data to s3://%s/%s for review -- these are real "
            "gaps in ESPN's own data, not something re-running this script will fix.",
            len(all_empty_events), storage.raw_bucket, empty_key,
        )

    if all_failures:
        failure_key = f"pga/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry only the missing tournaments",
            len(all_failures), storage.raw_bucket, failure_key,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
