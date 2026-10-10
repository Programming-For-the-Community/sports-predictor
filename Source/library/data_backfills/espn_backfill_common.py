"""
Shared ESPN backfill primitives (nba/ncaambb -- confirmed byte-identical
before sharing here, aside from the sport string used in S3 key prefixes).
nfl's own backfill.py differs structurally (week/season-type loop, no
process_date) and has no test coverage to verify a merge against, so it
stays fully separate; ncaafb/f1/pga are structurally distinct (no team
seeding, round-based fetches) and are also untouched.

Each sport's own backfill.py keeps `seed_teams(client, storage)` and
`process_game(client, storage, season, event_id)` under their original,
tested signatures, delegating to `seed_teams`/`process_game` below with
their own `normalize` module and sport string bound in. Safe to share
because both only call through `normalize_module` as a MODULE reference,
not individually-imported bare function names -- `patch.object(backfill.
normalize, "team_to_entity", ...)` mutates the one module object the
shared function's own `normalize_module.team_to_entity(...)` call reads
too.

`process_date`/`process_season`/`process_batch`/`main` stay fully
per-sport -- ncaambb adds AP-poll ranking seeding and per-event
ThreadPoolExecutor concurrency that nba doesn't have, and nba has a
preseason-skip check ncaambb doesn't need (see each sport's own module
docstring); not the same shape despite `seed_teams`/`process_game` being
identical.

`parse_args`/`run_batches` are the command-line and batch-running shell
around those per-sport functions (used by nhl).
"""
import argparse
import concurrent.futures
import os
import sys
import time
from datetime import datetime, timezone

from library.storage.pipeline_storage import PipelineStorage


def seed_teams(client, storage: PipelineStorage, normalize_module, sport: str, logger) -> None:
    """ESPN's /teams isn't season-scoped, so one global call seeds every team."""
    logger.info("Seeding team entities")
    teams_response = client.get_teams()
    storage.put_raw_json(f"{sport}/teams.json", teams_response)
    league = teams_response["sports"][0]["leagues"][0]
    for team_entry in league["teams"]:
        storage.upsert_entity(normalize_module.team_to_entity(team_entry["team"]))
    logger.info("Seeded %d teams", len(league["teams"]))


def process_game(client, storage: PipelineStorage, normalize_module, sport: str, season: int, event_id: str, logger) -> None:
    raw_key = f"{sport}/boxscore/{season}/{event_id}.json"
    if storage.raw_object_exists(raw_key):
        logger.debug("Box score already loaded, skipping event %s", event_id)
        return
    summary = client.get_summary(event_id)
    storage.put_raw_json(raw_key, summary)
    stats_items, player_entities = normalize_module.boxscore_to_player_game_stats(summary)
    for entity in player_entities:
        storage.upsert_player_entity(entity)
    storage.write_player_game_stats(stats_items)
    storage.write_team_game_stats(normalize_module.boxscore_to_team_game_stats(summary))


def parse_args(description: str, default_start_season: int, default_end_season: int) -> argparse.Namespace:
    """--start-season/--end-season/--batch-size/--request-delay, each
    defaulting to its environment variable (START_SEASON, END_SEASON,
    BATCH_SIZE, REQUEST_DELAY_SECONDS)."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--start-season", type=int, default=int(os.environ.get("START_SEASON", default_start_season)))
    parser.add_argument("--end-season", type=int, default=int(os.environ.get("END_SEASON", default_end_season)))
    parser.add_argument(
        "--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 2)),
        help="Seasons per concurrent worker",
    )
    parser.add_argument(
        "--request-delay", type=float, default=float(os.environ.get("REQUEST_DELAY_SECONDS", 0.3)),
        help="Minimum seconds between any two ESPN requests, enforced across all workers combined",
    )
    return parser.parse_args()


def run_batches(batches: list[list[int]], process_batch, storage: PipelineStorage, sport: str, logger) -> None:
    """Runs process_batch(batch) for every batch concurrently, logs the
    totals, and -- when any game failed -- writes the failures to
    {sport}/backfill-failures/ and exits 1. A batch that raises is logged
    and the others still report."""
    all_results = []
    start_time = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches), thread_name_prefix="batch") as executor:
        futures = {executor.submit(process_batch, batch): batch for batch in batches}
        for future in concurrent.futures.as_completed(futures):
            try:
                all_results.extend(future.result())
            except Exception:  # noqa: BLE001
                logger.exception("Batch %s raised an unhandled exception", futures[future])

    elapsed = time.monotonic() - start_time
    total_games = sum(r["games_processed"] for r in all_results)
    total_failed = sum(r["games_failed"] for r in all_results)
    all_failures = [failure for r in all_results for failure in r["failures"]]
    logger.info("Backfill complete in %.1fs: %d games processed, %d failed", elapsed, total_games, total_failed)

    if all_failures:
        failure_key = f"{sport}/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry only the missing games",
            len(all_failures), storage.raw_bucket, failure_key,
        )
        sys.exit(1)
