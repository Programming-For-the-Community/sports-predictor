"""
NHL ingest Lambda. Triggered daily by the shared ingest-orchestrator Step
Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention. Fetches yesterday's scoreboard and completed games' box
scores, the league's team list (nhl/teams.json) and every team's roster,
and writes it all as raw JSON to S3; the normalize Lambda is triggered by
the resulting S3 PutObject events, so this function never touches
DynamoDB directly.

Defaults to yesterday: games finish after this Lambda's early-morning
run would see them. EventBridge can override the target date via the
orchestrator's input payload: { "date": "20261009" } (YYYYMMDD).

Every team's roster is refreshed on every run, uncached, to catch a
roster move as soon as possible. The roster response embeds each
athlete's current injury status, attached onto each scoreboard event
before it's written to S3.

The scoreboard already carries each team's probable starting goalie and
each summary carries the game's play-by-play, so neither needs a fetch of
its own.

Preseason, All-Star and international-tournament games are never
ingested (library.normalize.nhl.is_ingestable_event).
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.aws.account import get_account_id
from library.aws.boto_config import DEFAULT_CONFIG
from library.http.nhl import NHLClient
from library.normalize.espn import attach_injuries, league_team_ids, roster_to_team_injuries
from library.normalize.nhl import CURRENT_INJURY_STATUSES, is_ingestable_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nhl-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

_s3 = boto3.client("s3", config=DEFAULT_CONFIG)


def _yesterday(today: date | None = None) -> str:
    return ((today or date.today()) - timedelta(days=1)).strftime("%Y%m%d")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key, ExpectedBucketOwner=get_account_id())
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(
        Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json",
        ExpectedBucketOwner=get_account_id(),
    )
    logger.info("Wrote s3://%s/%s", RAW_BUCKET, key)


def _fetch_rosters(client: NHLClient, team_ids: list[str]) -> tuple[int, int, dict[str, list[dict]]]:
    """Fetches and writes every team's current roster, one S3 object per
    team. Best-effort per team. Also returns injuries_by_team, extracted
    from the same responses; a team whose fetch failed has no entry."""
    fetched = failed = 0
    injuries_by_team: dict[str, list[dict]] = {}
    for team_id in team_ids:
        try:
            roster = client.get_roster(team_id)
            _put_json(f"nhl/roster/{team_id}.json", roster)
            injuries_by_team[team_id] = roster_to_team_injuries(roster, CURRENT_INJURY_STATUSES)
            fetched += 1
        except Exception:
            logger.exception("Failed fetching roster for team %s", team_id)
            failed += 1
    return fetched, failed, injuries_by_team


def lambda_handler(event: dict, context) -> dict:
    target_date = event.get("date") or _yesterday()
    client = NHLClient()

    teams_response = client.get_teams()
    _put_json("nhl/teams.json", teams_response)
    team_ids = league_team_ids(teams_response)

    rosters_fetched, rosters_failed, injuries_by_team = _fetch_rosters(client, team_ids)
    logger.info("Rosters: %d fetched, %d failed", rosters_fetched, rosters_failed)

    scoreboard = client.get_scoreboard_for_date(target_date)
    all_events = scoreboard.get("events", [])
    events = [evt for evt in all_events if is_ingestable_event(evt)]
    logger.info("Found %d events for date %s (%d ingestable)", len(all_events), target_date, len(events))

    result = {"processed": 0, "skipped": 0, "failed": 0, "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed}
    if not events:
        return result

    # Mutates the events inside scoreboard, so the payload written below
    # carries the injuries.
    attach_injuries(events, injuries_by_team)
    _put_json(f"nhl/scoreboard/{target_date}.json", scoreboard)

    for evt in events:
        event_id = evt["id"]

        if not evt.get("status", {}).get("type", {}).get("completed", False):
            logger.debug("Skipping incomplete event %s", event_id)
            result["skipped"] += 1
            continue

        raw_key = f"nhl/boxscore/{evt['season']['year']}/{event_id}.json"
        if _object_exists(raw_key):
            logger.debug("Box score already in S3, skipping event %s", event_id)
            result["skipped"] += 1
            continue

        try:
            _put_json(raw_key, client.get_summary(event_id))
            result["processed"] += 1
        except Exception:
            logger.exception("Failed fetching summary for event %s", event_id)
            result["failed"] += 1

    logger.info("Done: %d processed, %d skipped, %d failed", result["processed"], result["skipped"], result["failed"])
    return result
