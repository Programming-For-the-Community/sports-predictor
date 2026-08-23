"""
Live score/status cache for NCAA MBB events currently in or near their
scheduled tip-off. Never writes to DynamoDB -- this is a short-lived,
UI-display-only cache in S3, refreshed on its own schedule
(scheduler-ncaambb-live-scores.tf, every 60s) and read back by GET
/ncaambb/live-scores (this same Lambda, see handler.py).

NCAA MBB's data source and live-scores source are both ESPN, so refresh()
looks up a candidate directly by event id in that day's ESPN scoreboard
response, no abbreviation+date join fallback needed.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.normalize.espn import boxscore_to_player_game_stats
from library.parsing import parse_number

logger = logging.getLogger("ncaambb-live-scores")

LIVE_SCORES_CACHE_KEY = "ncaambb/cache/live-scores/latest.json"

# Same compound-key shape as aws-lambdas/ncaambb/normalize/handler.py's own
# _COMPOUND_KEY_SPLITS (basketball -- identical to NBA's) -- duplicated
# here rather than imported across Lambda package boundaries. Must stay in
# sync with normalize's copy.
_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "fieldGoalsMade-fieldGoalsAttempted": ("field_goals_made", "field_goal_attempts"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("three_pointers_made", "three_point_attempts"),
    "freeThrowsMade-freeThrowsAttempted": ("free_throws_made", "free_throw_attempts"),
}

# NCAA MBB can have 100+ tip-offs clustered on a given night -- the
# largest per-night volume of any sport onboarded so far (see
# project-ncaambb-onboarding memory), so this is raised above NBA's 10.
# Still below ingest's own _INGEST_MAX_WORKERS=8-per-call-type cap's spirit
# -- 15 is a deliberate middle ground, not the full plausible peak overlap.
BOXSCORE_MAX_WORKERS = 15

# 15 minutes catches an early tip-off without waiting on the scheduled
# time; 7 hours is a generous safety cap no real game approaches; 5
# minutes is how stale a cached read is allowed to look before a reader
# should treat it as unknown.
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
    """Events worth checking against ESPN's live scoreboard this cycle."""
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


def _live_player_stats(client, sport: str, event_id: str) -> dict[str, dict]:
    """Best-effort entity_id -> stat_line for one currently-live event,
    from ESPN's own boxscore/summary endpoint. Empty on any fetch/parse
    failure rather than raising -- one bad event shouldn't cost every
    other live event its own score/stat refresh this tick."""
    try:
        summary = client.get_summary(event_id)
        stats_items, _ = boxscore_to_player_game_stats(summary, sport, _COMPOUND_KEY_SPLITS)
        return {item["entity_id"]: item["stat_line"] for item in stats_items}
    except Exception:
        logger.exception("Failed fetching live box score for event %s -- omitting player_stats this tick", event_id)
        return {}


def refresh(storage, s3, bucket: str, client, sport: str) -> dict:
    """Called on every LiveScoreRefresh tick -- see scheduler-ncaambb-live-
    scores.tf. Cheap on every tick where nothing is in its live window:
    reads already-ingested events from DynamoDB and only reaches out to
    ESPN once _candidate_events finds something worth checking."""
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
    live_event_ids = []
    for event in candidates:
        espn_event = espn_events_by_id.get(event["event_id"])
        if espn_event is None:
            logger.warning("Candidate event %s not found in today's ESPN scoreboard -- skipping", event["event_id"])
            continue
        state = _extract_live_state(espn_event)
        events_out[event["event_id"]] = state
        if state["live"]:
            live_event_ids.append(event["event_id"])

    # Boxscore fetch is its own ESPN call per event, unlike the score/status
    # above -- only worth paying for an event ESPN itself confirms is
    # actually being played right now, not merely in the poll window.
    if live_event_ids:
        with ThreadPoolExecutor(max_workers=min(len(live_event_ids), BOXSCORE_MAX_WORKERS)) as executor:
            player_stats_by_event = dict(zip(
                live_event_ids,
                executor.map(lambda event_id: _live_player_stats(client, sport, event_id), live_event_ids),
            ))
        for event_id, player_stats in player_stats_by_event.items():
            events_out[event_id]["player_stats"] = player_stats

    _put_cache(s3, bucket, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


def get_live_scores(s3, bucket: str) -> dict:
    """Called by GET /ncaambb/live-scores (handler.py). Returns an empty
    events dict rather than an error on a cache miss or stale cache."""
    cache = _get_cache(s3, bucket)
    if cache is None:
        return {"events": {}}

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    if datetime.now(timezone.utc) - fetched_at > STALE_AFTER:
        logger.warning("Live-scores cache is stale (fetched_at=%s) -- serving empty", cache["fetched_at"])
        return {"events": {}}

    return {"events": cache.get("events", {})}
