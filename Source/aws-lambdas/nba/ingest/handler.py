"""
NBA ingest Lambda. Triggered daily by the shared ingest-orchestrator Step
Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention -- no separate per-sport EventBridge Scheduler is needed for
this Lambda (same pattern as NCAAFB's own ingest -- see that module's own
docstring). Fetches yesterday's completed games' box scores plus that
same date's scoreboard, the full 30-team league list (nba/teams.json --
what normalize's team_to_entity derives team display name/abbreviation/
color from), and every team's roster, and writes it all as raw JSON to
S3; the normalize Lambda is triggered automatically by the resulting S3
PutObject events, so this function never touches DynamoDB directly.

ESPN's NBA scoreboard is date-based, not week-based like NFL/NCAAFB (see
NBAClient.get_scoreboard_for_date) -- there is no "most recent Sunday"
equivalent to resolve, since games happen most nights during the season.
Defaults to YESTERDAY (not today): games mostly tip off in the evening
and finish after this Lambda's own early-morning scheduled run would see
them, so "yesterday" is the date most likely to have final scores/box
scores ready, the same role "most recent Sunday" plays for NFL's own
auto-detection. Today's/future dates are seeded ahead of time by
Terraform/scheduler-nba-schedule-sync.tf's dedicated nba-schedule-sync
Lambda, same division of responsibility as NFL's.
NOT yet verified against a real overnight run for a game that tips off
close to the UTC day boundary -- revisit if a late-slate game's box score
is ever missing the morning after (see project-nba-onboarding memory).

EventBridge can override the target date via the orchestrator's input
payload: { "date": "20260114" } (YYYYMMDD, matching NBAClient's own
param shape) -- for reprocessing one specific past date.

Also refreshes every one of the league's 30 teams' current full roster on
every run, unconditionally, uncached -- same reasoning as NFL's own
ingest (aws-lambdas/nfl/ingest/handler.py's own docstring): this exists
specifically to catch a roster move as soon as possible, so caching it
across days would defeat its own purpose. No depth-chart or coach
enrichment here, unlike NFL's ingest -- basketball has no depth-chart
concept, and coach-tenure features are out of this phase's approved scope
(deferred, not silently built -- see project-nba-onboarding memory).
NBA's own roster response already embeds each athlete's current injury
status and the team's head coach directly (confirmed live, 2026-08-14),
so unlike NFL there's no separate injury-report or coach-lookup call
needed at all for what data IS captured here -- that embedded data isn't
wired into any feature yet (deferred to Sub-phase 3A step 5, feature
engineering), but is written to S3 either way as part of the roster
payload's own shape.

Preseason (season type 1) is never ingested, whether auto-detected or
passed explicitly -- confirmed live, 2026-08-14, that NBA's season.type
uses the identical 1=preseason/2=regular/3=postseason convention NFL's
PRESEASON_TYPE constant already relies on. Backup-heavy preseason
rosters/results aren't representative of regular-season performance and
would skew training data, same reasoning as NFL/NCAAFB.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.http.nba import NBAClient

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


def _fetch_rosters(client: NBAClient, team_ids: list[str]) -> tuple[int, int]:
    """Fetches and writes every NBA team's current roster -- one S3
    object per team, always fresh, never TTL-cached (see this module's own
    docstring for why). Best-effort per team, same convention as NFL's
    own _fetch_rosters -- one team's fetch failing shouldn't lose the
    others'."""
    fetched = failed = 0
    for team_id in team_ids:
        try:
            roster = client.get_roster(team_id)
            _put_json(f"nba/roster/{team_id}.json", roster)
            fetched += 1
        except Exception:
            logger.exception("Failed fetching roster for team %s", team_id)
            failed += 1
    return fetched, failed


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
    rosters_fetched, rosters_failed = _fetch_rosters(client, team_ids)
    logger.info("Rosters: %d fetched, %d failed", rosters_fetched, rosters_failed)

    scoreboard = client.get_scoreboard_for_date(target_date)
    events = scoreboard.get("events", [])
    logger.info("Found %d events for date %s", len(events), target_date)

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
