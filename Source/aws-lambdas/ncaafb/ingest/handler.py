"""
NCAAFB ingest Lambda. Triggered daily by the shared ingest-orchestrator
Step Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention -- no separate per-sport EventBridge Scheduler is needed for
this Lambda (unlike schedule-sync/live-scores below, which ARE invoked
directly by their own scheduler). Fetches the current week's CFBD games
plus player/team box scores and writes raw JSON to S3; the normalize
Lambda is triggered automatically by the resulting S3 PutObject events,
so this function never touches DynamoDB directly.

CFBD has no per-date scoreboard lookup like ESPN's -- the current season/
week is resolved via CFBDClient.get_calendar (see _resolve_current_week),
confirmed live against real 2025 data. The invocation payload can override
auto-detection, same convention as NFL's own ingest:
    { "season": 2025, "week": 4, "season_type": "regular" }
All three must be given together for a manual override.

Also refreshes the season-teams (library.storage.ncaafb_team_cache) and
season-coaches (enrichment.py) S3 caches on every run, unconditionally --
same cadence reasoning as NFL's own ingest: both are cheap, TTL-backed
bulk calls, and refreshing daily just guarantees the cache gets a chance
to turn over rather than staying stale for a whole week.

No injury or depth-chart equivalent exists for college football (see
project plan) -- enrichment here is limited to coach info, current AP
rank, and venue_indoor (see enrichment.enrich_games).

Roster ingestion (CFBD's bulk /roster, monthly cadence per the project
plan) is NOT wired in here yet -- deferred as a small follow-on, not
silently dropped; player entities from this phase come only from box
scores, so metadata.position stays None until that lands (see
library/normalize/ncaafb.py's own docstring).
"""
import json
import logging
import os
from datetime import date, datetime, timezone

import boto3

import enrichment
from library.http.cfbd import CFBDClient
from library.storage.ncaafb_team_cache import get_cached_teams

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

_s3 = boto3.client("s3")


def _current_ncaafb_season(today: date | None = None) -> int:
    """The college football season year for `today`: Aug-Dec resolves to
    this year (the season in progress), January resolves to last year
    (still finishing the CFP championship, played in mid-to-late
    January), and Feb-Jul resolves to this year (the upcoming,
    not-yet-started season)."""
    today = today or date.today()
    return today.year - 1 if today.month == 1 else today.year


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolve_current_week(client: CFBDClient, season: int, today: date | None = None) -> tuple[str, int] | None:
    """Picks the most-recently-started week as of `today` from CFBD's
    /calendar -- the role NFL's _most_recent_sunday plays via ESPN's
    per-date scoreboard lookup, which CFBD has no equivalent of. Returns
    (season_type, week), or None if no week in the season's calendar has
    started yet."""
    today = today or date.today()
    now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)

    started = [
        week for week in client.get_calendar(season)
        if week.get("firstGameStart") and _parse_iso(week["firstGameStart"]) <= now
    ]
    if not started:
        return None

    latest = max(started, key=lambda week: week["firstGameStart"])
    return latest.get("seasonType", "regular"), latest["week"]


def _annotate_box_scores(box_scores: list[dict], games_by_id: dict[str, dict]) -> None:
    """Injects home_id/away_id/event_date into each per-game box score
    entry from that week's own /games response, resolved by game id --
    CFBD's box score endpoints carry no numeric team id or event date of
    their own (see cfbd.py's get_game_player_stats/get_game_team_stats
    docstrings), unlike ESPN's box scores which embed everything needed.
    Doing this once at ingest time (which already has both responses in
    hand) means normalize never needs a second CFBD/DynamoDB lookup to
    join the two -- same "enrich before persisting to S3" pattern
    enrichment.py's coach/ranking attachment already uses."""
    for entry in box_scores:
        game = games_by_id.get(str(entry.get("id")))
        if game is None:
            continue
        entry["home_id"] = str(game["homeId"])
        entry["away_id"] = str(game["awayId"])
        entry["event_date"] = (game.get("startDate") or "")[:10] or None


def _put_json(key: str, payload) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season")
    week = event.get("week")
    season_type = event.get("season_type")

    client = CFBDClient()
    resolved_season = season or _current_ncaafb_season()

    # Unconditional -- see this module's own docstring for why these run
    # before week resolution rather than being gated behind it. Wrapped
    # individually (not left to propagate) so a transient CFBD failure on
    # either cache refresh can't take down the whole run -- same
    # best-effort convention as enrich_games' own coach/ranking fetches.
    try:
        get_cached_teams(_s3, RAW_BUCKET, client, resolved_season)
        teams_cached = True
    except Exception:
        logger.exception("Failed fetching season teams for %d", resolved_season)
        teams_cached = False

    try:
        enrichment.get_cached_coaches(_s3, RAW_BUCKET, client, resolved_season)
        coaches_cached = True
    except Exception:
        logger.exception("Failed fetching season coaches for %d", resolved_season)
        coaches_cached = False

    if season is None or week is None or season_type is None:
        resolved = _resolve_current_week(client, resolved_season)
        if resolved is None:
            logger.info("No week has started yet for season %d -- nothing to ingest", resolved_season)
            return {
                "processed": 0, "failed": 0,
                "teams_cached": teams_cached, "coaches_cached": coaches_cached,
            }
        season_type, week = resolved
        season = resolved_season
        logger.info("Auto-detected season %d type %s week %d", season, season_type, week)

    games = client.get_games(season, week=week, season_type=season_type)
    logger.info("Found %d games in season %d type %s week %d", len(games), season, season_type, week)

    enrichment.enrich_games(games, season, week, client, _s3, RAW_BUCKET)
    _put_json(f"ncaafb/games/{season}/{season_type}/{week}.json", games)

    games_by_id = {str(g["id"]): g for g in games}

    processed = failed = 0
    completed_games = [g for g in games if g.get("completed")]
    if not completed_games:
        logger.info("No completed games yet in season %d type %s week %d -- box scores not fetched", season, season_type, week)
    else:
        # Bulk per-week calls, always re-fetched (no per-game idempotency
        # skip like NFL's own ingest) -- CFBD's box score endpoints cost
        # one call per week regardless of how many of that week's games
        # are done, so re-fetching daily while a week is still in
        # progress just picks up newly-completed games at the same cost
        # as skipping would have saved. Matches the project plan's own
        # "batched per week-since-last-run, not per-game" ingest design.
        try:
            player_box_scores = client.get_game_player_stats(season, week, season_type)
            _annotate_box_scores(player_box_scores, games_by_id)
            _put_json(f"ncaafb/boxscore/{season}/{season_type}/{week}.json", player_box_scores)
            processed += 1
        except Exception:
            logger.exception("Failed fetching player box scores for season %d type %s week %d", season, season_type, week)
            failed += 1

        try:
            team_box_scores = client.get_game_team_stats(season, week, season_type)
            _annotate_box_scores(team_box_scores, games_by_id)
            _put_json(f"ncaafb/teamstats/{season}/{season_type}/{week}.json", team_box_scores)
            processed += 1
        except Exception:
            logger.exception("Failed fetching team box scores for season %d type %s week %d", season, season_type, week)
            failed += 1

    logger.info("Done: %d processed, %d failed", processed, failed)
    return {
        "processed": processed, "failed": failed,
        "teams_cached": teams_cached, "coaches_cached": coaches_cached,
    }
