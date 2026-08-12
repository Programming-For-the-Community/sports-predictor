"""
Live score/status cache for NCAAFB events currently in or near their
scheduled kickoff -- the NCAAFB equivalent of aws-lambdas/nfl/live-scores/
live_scores.py. Deliberately never writes to DynamoDB, same reasoning as
NFL's own module: this is a short-lived, UI-display-only cache in S3,
refreshed on its own schedule (scheduler-ncaafb-live-scores.tf, every 60s)
and read back by GET /ncaafb/live-scores (this same Lambda, see handler.py).

CFBD's own ids (game/team/player) are confirmed to share ESPN's exact
numbering (live-verified, see project memory), so refresh() can look up a
candidate directly by event id in that day's ESPN scoreboard response --
the same direct-lookup shape NFL's own module uses, no crosswalk needed.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.parsing import parse_number

logger = logging.getLogger("ncaafb-live-scores")

LIVE_SCORES_CACHE_KEY = "ncaafb/cache/live-scores/latest.json"

# Same values as NFL's own module -- see that file's own comments for the
# reasoning (15 minutes catches an early kickoff without waiting on the
# scheduled time; 7 hours is a generous safety cap no real game approaches;
# 5 minutes is how stale a cached read is allowed to look before a reader
# should treat it as unknown).
POLL_START_BEFORE_KICKOFF = timedelta(minutes=15)
POLL_SAFETY_CAP_AFTER_KICKOFF = timedelta(hours=7)
STALE_AFTER = timedelta(minutes=5)


def _get_cache(s3, bucket: str) -> dict | None:
    try:
        response = s3.get_object(Bucket=bucket, Key=LIVE_SCORES_CACHE_KEY)
        return json.loads(response["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return None  # cache miss or malformed entry -- treat as "nothing cached yet"


def _put_cache(s3, bucket: str, payload: dict) -> None:
    s3.put_object(
        Bucket=bucket, Key=LIVE_SCORES_CACHE_KEY,
        Body=json.dumps(payload), ContentType="application/json",
    )


def _parse_kickoff(kickoff_time: str) -> datetime:
    return datetime.fromisoformat(kickoff_time.replace("Z", "+00:00"))


def _candidate_events(storage, sport: str, now: datetime, already_completed: set[str]) -> list[dict]:
    """Same shape and reasoning as NFL's own _candidate_events -- events
    worth checking against ESPN's live scoreboard this cycle."""
    candidates = []
    for event in storage.get_all_events(sport, status="scheduled"):
        event_id = event["event_id"]
        if event_id in already_completed:
            continue
        kickoff_time = event.get("kickoff_time")
        if kickoff_time is None:
            continue
        kickoff = _parse_kickoff(kickoff_time)
        if kickoff - POLL_START_BEFORE_KICKOFF <= now <= kickoff + POLL_SAFETY_CAP_AFTER_KICKOFF:
            candidates.append(event)
    return candidates


def _extract_live_state(espn_event: dict) -> dict:
    competition = espn_event["competitions"][0]
    status_type = competition.get("status", {}).get("type", {})
    scores = {c.get("homeAway"): parse_number(c.get("score")) for c in competition.get("competitors", [])}
    return {
        "live": status_type.get("state") == "in",
        "completed": bool(status_type.get("completed")),
        "detail": status_type.get("shortDetail"),
        "home_score": scores.get("home"),
        "away_score": scores.get("away"),
    }


def refresh(storage, s3, bucket: str, client, sport: str) -> dict:
    """Called on every LiveScoreRefresh tick -- see scheduler-ncaafb-live-
    scores.tf. Cheap on every tick where nothing is in its live window,
    same as NFL's own refresh: reads already-ingested events from
    DynamoDB and only reaches out to ESPN once _candidate_events finds
    something worth checking."""
    now = datetime.now(timezone.utc)

    previous = _get_cache(s3, bucket) or {}
    already_completed = {
        event_id for event_id, state in previous.get("events", {}).items() if state.get("completed")
    }

    candidates = _candidate_events(storage, sport, now, already_completed)
    if not candidates:
        logger.info("No events in a live-poll window -- skipping ESPN call")
        return {"polled": 0}

    scoreboard = client.get_scoreboard_for_date(now.strftime("%Y%m%d"))
    espn_events_by_id = {e["id"]: e for e in scoreboard.get("events", [])}

    events_out = {}
    for event in candidates:
        espn_event = espn_events_by_id.get(event["event_id"])
        if espn_event is None:
            logger.warning("Candidate event %s not found in today's ESPN scoreboard -- skipping", event["event_id"])
            continue
        events_out[event["event_id"]] = _extract_live_state(espn_event)

    _put_cache(s3, bucket, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


def get_live_scores(s3, bucket: str) -> dict:
    """Called by GET /ncaafb/live-scores (handler.py). Same empty-not-
    error/staleness contract as NFL's own get_live_scores."""
    cache = _get_cache(s3, bucket)
    if cache is None:
        return {"events": {}}

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    if datetime.now(timezone.utc) - fetched_at > STALE_AFTER:
        logger.warning("Live-scores cache is stale (fetched_at=%s) -- serving empty", cache["fetched_at"])
        return {"events": {}}

    return {"events": cache.get("events", {})}
