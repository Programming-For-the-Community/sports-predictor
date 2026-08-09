"""
NCAAFB historical backfill job -- the CFBD-sourced equivalent of
data-backfills/nfl/backfill.py.

Pulls games, player/team box scores, coach, and AP Top 25 ranking data
from CollegeFootballData.com (CFBD) for a range of seasons and loads it
into the raw data lake (S3) and normalized tables (DynamoDB). Safe to
re-run at any time: every DynamoDB write is an upsert (put_item on the
same key). Unlike NFL's per-game raw_object_exists skip, CFBD's box
score endpoints are bulk-per-week (see library/http/cfbd.py) and cost
the same one call per week regardless of how many of that week's games
are already loaded, so a week's box scores are always re-fetched rather
than skipped -- same design ingest/handler.py's own daily run already
uses.

Team entities are seeded ONCE, from END_SEASON's /teams response, not
once per season -- CFBD's /teams is season-scoped (conference
realignment happens ~yearly) but a team entity row isn't a dated
snapshot, only a "current" one. Seeding from every season (this runs
seasons concurrently) would let whichever season's fetch happens to
finish last win the race for a given team's conference value; using the
most recent season keeps entities current, matching NFL's own single
get_teams() call.

Coach and AP Top 25 rank enrichment (the same fields ingest's own
enrich_games attaches per-week -- see aws-lambdas/ncaafb/ingest/
enrichment.py) are resolved once per SEASON here, not once per week:
teams and coaches don't change week to week, and CFBD's /rankings
returns every week of a season in one call when `week` is omitted --
confirmed live this session (2025 data: one call returned all 16 weeks,
same shape as /calendar's own no-week-returns-everything behavior).
Each AP rank entry also carries CFBD's own numeric teamId directly
(`{"rank","teamId","school","conference",...}`), not just school name --
not currently used (rank_lookup_by_school still joins by school name,
matching the ingest-side lookup this mirrors), but worth knowing as a
simpler alternative if that join ever needs revisiting. Rankings scoped
to season_type="regular" only -- whether/how CFBD's postseason bowl weeks
carry a "current" AP rank of their own is unverified, so postseason
games are left without home_current_rank/away_current_rank rather than
guessing at unverified behavior.

Seasons are split into batches (default: 2 seasons each) processed
concurrently by a thread pool, one thread per batch. All threads share a
single CFBDClient / rate limiter so N concurrent batches don't multiply
the request rate by N against CFBD's monthly call budget.

Required environment variables:
    RAW_BUCKET_NAME
    ENTITIES_TABLE_NAME
    EVENTS_TABLE_NAME
    PLAYER_GAME_STATS_TABLE_NAME
    TEAM_GAME_STATS_TABLE_NAME
    AWS_REGION
    CFBD_API_ROOT_URL
    CFBD_API_KEY_SECRET_ARN
    CFBD_API_KEY_SECRET_FIELD

Optional environment variables -- overridable per ECS "Run Task" console
launch via container overrides, without editing the task definition.
CLI flags, when passed, take precedence over both.
    START_SEASON (default 2015)
    END_SEASON (default 2025)
    BATCH_SIZE (default 2)
    REQUEST_DELAY_SECONDS (default 0.3)

Usage:
    python backfill.py --start-season 2015 --end-season 2025 --batch-size 2
"""
import argparse
import concurrent.futures
import logging
import os
import sys
import time
from datetime import datetime, timezone

import boto3

import normalize
from library.http.cfbd import CFBDClient
from library.storage.ncaafb_coach_cache import coach_lookup_by_school, get_cached_coaches, rank_lookup_by_school
from library.storage.ncaafb_team_cache import get_cached_teams, teams_by_id
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
)
logger = logging.getLogger("ncaafb-backfill")

# Starts at 1, not 0 -- confirmed live this session that CFBD's own
# server-side validation treats week=0 as "not provided" (a falsy-value
# bug on their end, not ours): GET /games/players and /games/teams both
# hard-reject week=0 with 400 ("either week, team, or conference are
# required"), and GET /games doesn't error but silently returns the
# ENTIRE season instead of filtering to week 0 (868 games for one 2023
# test call, not a real single week). There is no way to request week-0
# data specifically through this endpoint family -- any real "Week 0"
# season-opener games are a known, accepted gap, not silently mishandled.
# A week past the real season length is still harmless (empty games
# list), same "generous ceiling" convention as NFL's own
# REGULAR_SEASON_WEEKS/POSTSEASON_WEEKS.
REGULAR_SEASON_WEEKS = range(1, 17)
POSTSEASON_WEEKS = range(1, 6)
SEASON_TYPES = ("regular", "postseason")


def chunk_seasons(start: int, end: int, batch_size: int) -> list[list[int]]:
    seasons = list(range(start, end + 1))
    return [seasons[i:i + batch_size] for i in range(0, len(seasons), batch_size)]


def seed_teams(client: CFBDClient, storage: PipelineStorage, s3, bucket: str, season: int) -> None:
    logger.info("Seeding team entities from season %d", season)
    teams = get_cached_teams(s3, bucket, client, season)
    for team in teams:
        storage.upsert_entity(normalize.team_to_entity(team))
    logger.info("Seeded %d teams", len(teams))


def _season_rank_lookup(client: CFBDClient, storage: PipelineStorage, season: int) -> dict[int, dict[str, int]]:
    """{week: {school: rank}} for every week of `season`'s AP Top 25 poll,
    from ONE get_rankings call (week omitted) -- also lands the raw
    payload in S3 for feature-engineering's national-ranking model to
    consume directly (no DynamoDB table for rank history exists -- see
    project plan's "no new tables" storage design)."""
    weekly = client.get_rankings(season)
    storage.put_raw_json(f"ncaafb/rankings/{season}.json", weekly)

    lookup: dict[int, dict[str, int]] = {}
    for week_entry in weekly:
        week = week_entry.get("week")
        if week is None:
            continue
        lookup[week] = rank_lookup_by_school([week_entry])
    return lookup


def _attach_enrichment(
    games: list[dict],
    by_id: dict[str, dict],
    coach_by_school: dict[str, dict],
    rank_by_school: dict[str, int],
) -> None:
    """Attaches venue_indoor, home/away coach, and home/away current rank
    to each game dict in place -- the same fields ingest's own
    enrich_games attaches, resolved the same way (school name via
    `by_id`'s season-scoped /teams join), but from data already fetched
    once for the whole season rather than re-fetching per week."""
    for game in games:
        home_id = str(game["homeId"]) if game.get("homeId") is not None else None
        away_id = str(game["awayId"]) if game.get("awayId") is not None else None
        home_team = by_id.get(home_id) if home_id else None
        home_school = (home_team or {}).get("school")
        away_school = by_id.get(away_id, {}).get("school") if away_id else None

        game["venue_indoor"] = (home_team or {}).get("location", {}).get("dome")
        game["home_coach"] = coach_by_school.get(home_school)
        game["away_coach"] = coach_by_school.get(away_school)
        game["home_current_rank"] = rank_by_school.get(home_school)
        game["away_current_rank"] = rank_by_school.get(away_school)


def _annotate_box_scores(box_scores: list[dict], games_by_id: dict[str, dict]) -> None:
    """Injects home_id/away_id/event_date into each per-game box score
    entry from that week's own /games response -- same join
    aws-lambdas/ncaafb/ingest/handler.py's own _annotate_box_scores
    performs, duplicated here rather than shared since it's small,
    stateless glue and backfill can't import a sibling Lambda's local
    file (see library/storage/ncaafb_team_cache.py's own docstring for
    why shared logic lives in library/, not aws-lambdas/)."""
    for entry in box_scores:
        game = games_by_id.get(str(entry.get("id")))
        if game is None:
            continue
        entry["home_id"] = str(game["homeId"])
        entry["away_id"] = str(game["awayId"])
        entry["event_date"] = (game.get("startDate") or "")[:10] or None


def process_week(
    client: CFBDClient,
    storage: PipelineStorage,
    season: int,
    season_type: str,
    week: int,
    by_id: dict[str, dict],
    coach_by_school: dict[str, dict],
    rank_by_week: dict[int, dict[str, int]],
) -> dict:
    games_processed = games_failed = 0
    failures = []

    games = client.get_games(season, week=week, season_type=season_type)
    if not games:
        return {"games_processed": 0, "games_failed": 0, "failures": []}

    _attach_enrichment(games, by_id, coach_by_school, rank_by_week.get(week, {}))
    storage.put_raw_json(f"ncaafb/games/{season}/{season_type}/{week}.json", games)
    games_by_id = {str(g["id"]): g for g in games}

    for game in games:
        try:
            storage.upsert_event(normalize.game_to_event_item(game))
            games_processed += 1
        except Exception as exc:  # noqa: BLE001 -- log and continue, one bad game shouldn't kill the run
            games_failed += 1
            logger.exception("Failed writing event for game %s (season %s)", game.get("id"), season)
            failures.append({"season": season, "event_id": game.get("id"), "error": str(exc)})

    if not any(g.get("completed") for g in games):
        return {"games_processed": games_processed, "games_failed": games_failed, "failures": failures}

    try:
        player_box_scores = client.get_game_player_stats(season, week, season_type)
        _annotate_box_scores(player_box_scores, games_by_id)
        storage.put_raw_json(f"ncaafb/boxscore/{season}/{season_type}/{week}.json", player_box_scores)
        for box_score in player_box_scores:
            stats_items, player_entities = normalize.game_player_stats_to_player_game_stats(box_score)
            for entity in player_entities:
                storage.upsert_player_entity(entity)
            storage.write_player_game_stats(stats_items)
    except Exception as exc:  # noqa: BLE001
        games_failed += 1
        logger.exception("Failed player box scores for season %s type %s week %s", season, season_type, week)
        failures.append({"season": season, "week": week, "error": str(exc)})

    try:
        team_box_scores = client.get_game_team_stats(season, week, season_type)
        _annotate_box_scores(team_box_scores, games_by_id)
        storage.put_raw_json(f"ncaafb/teamstats/{season}/{season_type}/{week}.json", team_box_scores)
        for box_score in team_box_scores:
            storage.write_team_game_stats(normalize.game_team_stats_to_team_game_stats(box_score))
    except Exception as exc:  # noqa: BLE001
        games_failed += 1
        logger.exception("Failed team box scores for season %s type %s week %s", season, season_type, week)
        failures.append({"season": season, "week": week, "error": str(exc)})

    return {"games_processed": games_processed, "games_failed": games_failed, "failures": failures}


def process_season(client: CFBDClient, storage: PipelineStorage, s3, bucket: str, season: int) -> dict:
    logger.info("Fetching season-scoped enrichment for %d", season)
    try:
        by_id = teams_by_id(get_cached_teams(s3, bucket, client, season))
    except Exception:
        logger.exception("Failed fetching season teams for %d -- venue_indoor/coach/rank fields will be omitted", season)
        by_id = {}

    try:
        coach_by_school = coach_lookup_by_school(get_cached_coaches(s3, bucket, client, season), season)
    except Exception:
        logger.exception("Failed fetching season coaches for %d -- coach fields will be omitted", season)
        coach_by_school = {}

    try:
        rank_by_week = _season_rank_lookup(client, storage, season)
    except Exception:
        logger.exception("Failed fetching season rankings for %d -- rank fields will be omitted", season)
        rank_by_week = {}

    games_processed = games_failed = 0
    failures = []
    for season_type in SEASON_TYPES:
        weeks = REGULAR_SEASON_WEEKS if season_type == "regular" else POSTSEASON_WEEKS
        for week in weeks:
            result = process_week(client, storage, season, season_type, week, by_id, coach_by_school, rank_by_week)
            games_processed += result["games_processed"]
            games_failed += result["games_failed"]
            failures.extend(result["failures"])

    return {
        "season": season,
        "games_processed": games_processed,
        "games_failed": games_failed,
        "failures": failures,
    }


def process_batch(client: CFBDClient, storage: PipelineStorage, s3, bucket: str, seasons: list[int]) -> list[dict]:
    results = []
    for season in seasons:
        logger.info("Starting season %s", season)
        result = process_season(client, storage, s3, bucket, season)
        logger.info(
            "Finished season %s: %d games processed, %d failed",
            season, result["games_processed"], result["games_failed"],
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical NCAAFB data from CFBD into S3 and DynamoDB.")
    parser.add_argument("--start-season", type=int, default=int(os.environ.get("START_SEASON", 2015)))
    parser.add_argument("--end-season", type=int, default=int(os.environ.get("END_SEASON", 2025)))
    parser.add_argument(
        "--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", 2)),
        help="Seasons per concurrent worker",
    )
    parser.add_argument(
        "--request-delay", type=float, default=float(os.environ.get("REQUEST_DELAY_SECONDS", 0.3)),
        help="Minimum seconds between any two CFBD requests, enforced across all workers combined",
    )
    args = parser.parse_args()

    batches = chunk_seasons(args.start_season, args.end_season, args.batch_size)
    logger.info(
        "Running backfill for seasons %d-%d in %d batch(es) of up to %d season(s) each",
        args.start_season, args.end_season, len(batches), args.batch_size,
    )

    client = CFBDClient(min_interval_seconds=args.request_delay)
    storage = PipelineStorage()
    s3 = boto3.client("s3")

    seed_teams(client, storage, s3, storage.raw_bucket, args.end_season)

    all_results = []
    start_time = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches), thread_name_prefix="batch") as executor:
        futures = {
            executor.submit(process_batch, client, storage, s3, storage.raw_bucket, batch): batch
            for batch in batches
        }
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
        failure_key = f"ncaafb/backfill-failures/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        storage.put_raw_json(failure_key, {"failures": all_failures})
        logger.warning(
            "Wrote %d failures to s3://%s/%s -- re-running the script will retry the missing/failed weeks",
            len(all_failures), storage.raw_bucket, failure_key,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
