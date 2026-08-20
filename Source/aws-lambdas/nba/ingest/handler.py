"""
NBA ingest Lambda. Triggered daily by the shared ingest-orchestrator Step
Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention -- no separate per-sport EventBridge Scheduler is needed for
this Lambda. Fetches yesterday's completed games' box scores plus that
same date's scoreboard, the full 30-team league list (nba/teams.json --
what normalize's team_to_entity derives team display name/abbreviation/
color from), and every team's roster, and writes it all as raw JSON to
S3; the normalize Lambda is triggered automatically by the resulting S3
PutObject events, so this function never touches DynamoDB directly.

ESPN's NBA scoreboard is date-based, not week-based (see
NBAClient.get_scoreboard_for_date) -- there is no "most recent Sunday"
equivalent to resolve, since games happen most nights during the season.
Defaults to yesterday (not today): games mostly tip off in the evening
and finish after this Lambda's own early-morning scheduled run would see
them, so "yesterday" is the date most likely to have final scores/box
scores ready. Today's/future dates are seeded ahead of time by
Terraform/scheduler-nba-schedule-sync.tf's dedicated nba-schedule-sync
Lambda.

EventBridge can override the target date via the orchestrator's input
payload: { "date": "20260114" } (YYYYMMDD, matching NBAClient's own
param shape) -- for reprocessing one specific past date.

Also refreshes every one of the league's 30 teams' current full roster on
every run, unconditionally, uncached -- this exists specifically to catch
a roster move as soon as possible, so caching it across days would defeat
its own purpose. No depth-chart concept in basketball, and coach-tenure
features aren't built. NBA's own roster response already embeds each
athlete's current injury status and the team's head coach directly, so
there's no separate injury-report or coach-lookup call needed. Injuries
are wired in (_fetch_rosters/_attach_injuries below, attached onto each
scoreboard event before it's written to S3 -- see _attach_injuries).

Preseason (season type 1) is never ingested, whether auto-detected or
passed explicitly. Backup-heavy preseason rosters/results aren't
representative of regular-season performance and would skew training
data.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.http.nba import NBAClient
from library.normalize.espn import roster_to_team_injuries

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nba-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
PRESEASON_TYPE = 1

_s3 = boto3.client("s3")


def _yesterday(today: date | None = None) -> str:
    return ((today or date.today()) - timedelta(days=1)).strftime("%Y%m%d")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")
    logger.info("Wrote s3://%s/%s", RAW_BUCKET, key)


def _team_ids(teams_response: dict) -> list[str]:
    """Every one of the league's 30 team ids from a get_teams() response --
    not derived from any date's scoreboard, so this works identically in
    the off-season or on a night with no games at all."""
    leagues = teams_response.get("sports", [{}])[0].get("leagues", [{}])
    teams = leagues[0].get("teams", []) if leagues else []
    return [t["team"]["id"] for t in teams if t.get("team", {}).get("id")]


def _fetch_rosters(client: NBAClient, team_ids: list[str]) -> tuple[int, int, dict[str, list[dict]]]:
    """Fetches and writes every NBA team's current roster -- one S3
    object per team, always fresh, never TTL-cached (see this module's own
    docstring for why). Best-effort per team -- one team's fetch failing
    shouldn't lose the others'.

    Also returns injuries_by_team, keyed by team id -- extracted from the
    same roster responses being written to S3 here, no extra API call. A
    team whose own fetch failed simply has no entry, same best-effort
    handling _attach_injuries below applies."""
    fetched = failed = 0
    injuries_by_team: dict[str, list[dict]] = {}
    for team_id in team_ids:
        try:
            roster = client.get_roster(team_id)
            _put_json(f"nba/roster/{team_id}.json", roster)
            injuries_by_team[team_id] = roster_to_team_injuries(roster)
            fetched += 1
        except Exception:
            logger.exception("Failed fetching roster for team %s", team_id)
            failed += 1
    return fetched, failed, injuries_by_team


def _attach_injuries(events: list[dict], injuries_by_team: dict[str, list[dict]]) -> None:
    """Attaches home_injuries/away_injuries onto each scoreboard event
    dict in place, from the same run's roster fetch above.
    library.normalize.espn.scoreboard_event_to_event_item already reads
    home_injuries/away_injuries off the event dict generically (shared
    across every sport), so no normalize-side change is needed once this
    is set.

    Forward-only: only events processed by an ingest run from here
    forward carry this field -- historical backfilled events never had a
    same-day roster fetch to attach.

    Best-effort: a team missing from injuries_by_team (its own roster
    fetch failed above) simply leaves that side's field unset rather than
    writing an empty list, preserving the "never checked" vs. "checked,
    nobody's hurt" distinction scoreboard_event_to_event_item's own
    docstring documents."""
    for evt in events:
        competitions = evt.get("competitions") or [{}]
        for competitor in competitions[0].get("competitors", []):
            team_id = str(competitor.get("team", {}).get("id", ""))
            role = competitor.get("homeAway")
            if role not in ("home", "away") or team_id not in injuries_by_team:
                continue
            evt[f"{role}_injuries"] = injuries_by_team[team_id]


def lambda_handler(event: dict, context) -> dict:
    target_date = event.get("date") or _yesterday()
    client = NBAClient()

    # Written to S3 every run (uncached, like the roster fetch below) --
    # this is what normalize's team_to_entity call derives team display
    # name/abbreviation/color from (see library/normalize/espn.py). Also
    # the source of team ids for the roster fetch below, so one call
    # covers both rather than fetching /teams twice.
    teams_response = client.get_teams()
    _put_json("nba/teams.json", teams_response)
    team_ids = _team_ids(teams_response)

    # Unconditional -- see this module's own docstring for why this runs
    # regardless of the target date's season type below.
    rosters_fetched, rosters_failed, injuries_by_team = _fetch_rosters(client, team_ids)
    logger.info("Rosters: %d fetched, %d failed", rosters_fetched, rosters_failed)

    scoreboard = client.get_scoreboard_for_date(target_date)
    events = scoreboard.get("events", [])
    logger.info("Found %d events for date %s", len(events), target_date)
    # Mutates events in place -- events IS scoreboard["events"], so this
    # is reflected in the scoreboard payload written to S3 below.
    _attach_injuries(events, injuries_by_team)

    if events and events[0].get("season", {}).get("type") == PRESEASON_TYPE:
        logger.info("season.type=%d is preseason -- skipping, not ingested by design", PRESEASON_TYPE)
        return {
            "processed": 0, "skipped": 0, "failed": 0,
            "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed,
        }

    season = events[0]["season"]["year"] if events else None
    scoreboard_key = f"nba/scoreboard/{target_date}.json"
    _put_json(scoreboard_key, scoreboard)

    processed = skipped = failed = 0
    for evt in events:
        event_id = evt["id"]

        if not evt.get("status", {}).get("type", {}).get("completed", False):
            logger.debug("Skipping incomplete event %s", event_id)
            skipped += 1
            continue

        raw_key = f"nba/boxscore/{season}/{event_id}.json"
        if _object_exists(raw_key):
            logger.debug("Box score already in S3, skipping event %s", event_id)
            skipped += 1
            continue

        try:
            summary = client.get_summary(event_id)
            _put_json(raw_key, summary)
            processed += 1
        except Exception:
            logger.exception("Failed fetching summary for event %s", event_id)
            failed += 1

    logger.info("Done: %d processed, %d skipped, %d failed", processed, skipped, failed)
    return {
        "processed": processed, "skipped": skipped, "failed": failed,
        "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed,
    }
