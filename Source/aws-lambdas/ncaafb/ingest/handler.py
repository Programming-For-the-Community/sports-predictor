"""
NCAAFB ingest Lambda. Triggered daily by the shared ingest-orchestrator
Step Function, which invokes every active sport's own
"${project}-<sport>-ingest" Lambda by naming convention. Fetches the
current week's CFBD games plus player/team box scores and writes raw
JSON to S3; the normalize Lambda is triggered automatically by the
resulting S3 PutObject events, so this function never touches DynamoDB
directly.

CFBD has no per-date scoreboard lookup like ESPN's -- the current
season/week is resolved via CFBDClient.get_calendar (see
_resolve_current_week). The invocation payload can override
auto-detection:
    { "season": 2025, "week": 4, "season_type": "regular" }
All three must be given together for a manual override.

Also refreshes the season-teams (library.storage.ncaafb_team_cache) and
season-coaches (enrichment.py) S3 caches on every run, unconditionally.

No injury or depth-chart equivalent exists for college football --
enrichment here is limited to coach info, current AP rank, and
venue_indoor (see enrichment.enrich_games).

Also fetches CFBD's bulk /roster (see CFBDClient.get_roster) on a
monthly cadence (see _fetch_roster_if_stale below) and writes it to S3
under ncaafb/roster/{season}.json for normalize to pick up the same way
games/boxscore/teamstats already are; this backfills metadata.position
onto player entities, which box scores alone never set (CFBD's box-score
athletes carry no position field).

CFBD's /roster gives each player's team as a school name ("team"), not a
numeric id, unlike /games' home_id/away_id. Resolved to teamId here via
the same season-teams cache get_cached_teams already fetches.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import enrichment
from library.http.cfbd import CFBDClient
from library.storage.ncaafb_team_cache import get_cached_teams, teams_by_school

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
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
    /calendar. Returns (season_type, week), or None if no week in the
    season's calendar has started yet."""
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
    their own. Doing this once at ingest time (which already has both
    responses in hand) means normalize never needs a second CFBD/DynamoDB
    lookup to join the two."""
    for entry in box_scores:
        game = games_by_id.get(str(entry.get("id")))
        if game is None:
            continue
        entry["home_id"] = str(game["homeId"])
        entry["away_id"] = str(game["awayId"])
        entry["event_date"] = (game.get("startDate") or "")[:10] or None


def _put_json(key: str, payload) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def _annotate_roster(roster: list[dict], teams: list[dict]) -> None:
    """Injects teamId into each roster entry, resolved from its "team"
    school name via `teams` (that season's get_cached_teams result) --
    CFBD's /roster carries no numeric team id of its own, only the school
    name. A player whose school isn't in `teams` (e.g. a since-moved-on
    transfer) is left without a teamId; roster_to_player_entities already
    skips those."""
    by_school = teams_by_school(teams)
    for player in roster:
        team = by_school.get(player.get("team"))
        if team is not None:
            player["teamId"] = team["id"]


ROSTER_CACHE_TTL_DAYS = 30


def _roster_marker_key(season: int) -> str:
    return f"ncaafb/cache/roster-fetched-at/{season}.json"


def _season_kickoff(client: CFBDClient, season: int) -> datetime | None:
    """The season's own regular-season week-1 firstGameStart, or None if
    the calendar doesn't have one yet (e.g., well before the season)."""
    for week in client.get_calendar(season):
        if week.get("week") == 1 and week.get("seasonType", "regular") == "regular" and week.get("firstGameStart"):
            return _parse_iso(week["firstGameStart"])
    return None


def _roster_needs_refresh(season: int, season_kickoff: datetime | None) -> bool:
    """True if season's roster hasn't been fetched within
    ROSTER_CACHE_TTL_DAYS, OR if it was fetched before the season's own
    week-1 kickoff. CFBD's /roster is one ~9MB bulk call covering every
    team, and college rosters barely move outside the transfer portal
    windows, so a plain rolling TTL is normally enough to refresh
    monthly rather than daily -- but fall-camp cuts/walk-on promotions/
    late portal moves cluster right before kickoff, so a snapshot taken
    during camp can still read as "fresh" under the TTL alone for weeks
    into the season it predates entirely. Marker-file pattern, kept
    local to this Lambda since the roster payload has to be written to
    S3 for normalize to pick up."""
    try:
        response = _s3.get_object(Bucket=RAW_BUCKET, Key=_roster_marker_key(season))
        fetched_at = datetime.fromisoformat(json.loads(response["Body"].read())["fetched_at"])
    except (ClientError, json.JSONDecodeError, KeyError, ValueError):
        return True  # cache miss or malformed marker -- treat as never fetched
    if season_kickoff is not None and fetched_at < season_kickoff:
        return True
    return datetime.now(timezone.utc) - fetched_at >= timedelta(days=ROSTER_CACHE_TTL_DAYS)


def _fetch_roster_if_stale(client: CFBDClient, season: int, teams: list[dict]) -> bool:
    """Fetches and writes season's full roster to
    ncaafb/roster/{season}.json (picked up by normalize's own S3 trigger)
    if the TTL/kickoff marker says it's due. Returns True if a fetch
    actually happened, False on a cache hit.

    Wraps the payload in a {"fetched_at", "data"} envelope -- CFBD's
    /roster has no per-payload timestamp of its own, so normalize reads
    fetched_at from here for metadata.team_id_as_of.

    teams is that same season's get_cached_teams result, passed in by
    the caller rather than re-fetched here."""
    season_kickoff = _season_kickoff(client, season)
    if not _roster_needs_refresh(season, season_kickoff):
        logger.info(
            "Roster for season %s was already fetched within the last %d days and after kickoff -- skipping "
            "(delete %s to force a refresh)",
            season, ROSTER_CACHE_TTL_DAYS, _roster_marker_key(season),
        )
        return False
    roster = client.get_roster(season)
    _annotate_roster(roster, teams)
    now = datetime.now(timezone.utc).isoformat()
    _put_json(f"ncaafb/roster/{season}.json", {"fetched_at": now, "data": roster})
    _put_json(_roster_marker_key(season), {"fetched_at": now})
    logger.info("Fetched %d roster rows for season %s -> ncaafb/roster/%s.json", len(roster), season, season)
    return True


def lambda_handler(event: dict, context) -> dict:
    season = event.get("season")
    week = event.get("week")
    season_type = event.get("season_type")

    client = CFBDClient()
    resolved_season = season or _current_ncaafb_season()

    # Wrapped individually so a transient CFBD failure on either cache
    # refresh can't take down the whole run.
    try:
        season_teams = get_cached_teams(_s3, RAW_BUCKET, client, resolved_season)
        teams_cached = True
    except Exception:
        logger.exception("Failed fetching season teams for %s", resolved_season)
        season_teams, teams_cached = [], False

    try:
        enrichment.get_cached_coaches(_s3, RAW_BUCKET, client, resolved_season)
        coaches_cached = True
    except Exception:
        logger.exception("Failed fetching season coaches for %s", resolved_season)
        coaches_cached = False

    try:
        roster_fetched = _fetch_roster_if_stale(client, resolved_season, season_teams)
    except Exception:
        logger.exception("Failed fetching roster for %s", resolved_season)
        roster_fetched = False

    if season is None or week is None or season_type is None:
        resolved = _resolve_current_week(client, resolved_season)
        if resolved is None:
            logger.info("No week has started yet for season %s -- nothing to ingest", resolved_season)
            return {
                "processed": 0, "failed": 0,
                "teams_cached": teams_cached, "coaches_cached": coaches_cached, "roster_fetched": roster_fetched,
            }
        season_type, week = resolved
        season = resolved_season
        logger.info("Auto-detected season %s type %s week %s", season, season_type, week)

    games = client.get_games(season, week=week, season_type=season_type)
    logger.info("Found %d games in season %s type %s week %s", len(games), season, season_type, week)

    enrichment.enrich_games(games, season, week, client, _s3, RAW_BUCKET)
    _put_json(f"ncaafb/games/{season}/{season_type}/{week}.json", games)

    games_by_id = {str(g["id"]): g for g in games}

    processed = failed = 0
    completed_games = [g for g in games if g.get("completed")]
    if not completed_games:
        logger.info("No completed games yet in season %s type %s week %s -- box scores not fetched", season, season_type, week)
    else:
        # Bulk per-week calls, always re-fetched -- CFBD's box score
        # endpoints cost one call per week regardless of how many of that
        # week's games are done, so re-fetching daily while a week is
        # still in progress just picks up newly-completed games.
        try:
            player_box_scores = client.get_game_player_stats(season, week, season_type)
            _annotate_box_scores(player_box_scores, games_by_id)
            _put_json(f"ncaafb/boxscore/{season}/{season_type}/{week}.json", player_box_scores)
            processed += 1
        except Exception:
            logger.exception("Failed fetching player box scores for season %s type %s week %s", season, season_type, week)
            failed += 1

        try:
            team_box_scores = client.get_game_team_stats(season, week, season_type)
            _annotate_box_scores(team_box_scores, games_by_id)
            _put_json(f"ncaafb/teamstats/{season}/{season_type}/{week}.json", team_box_scores)
            processed += 1
        except Exception:
            logger.exception("Failed fetching team box scores for season %s type %s week %s", season, season_type, week)
            failed += 1

    logger.info("Done: %d processed, %d failed", processed, failed)
    return {
        "processed": processed, "failed": failed,
        "teams_cached": teams_cached, "coaches_cached": coaches_cached, "roster_fetched": roster_fetched,
    }
