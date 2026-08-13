"""
Live score/status cache for events currently in or near their scheduled
kickoff -- see handler.py's LiveScoreRefresh trigger. Deliberately never
writes to DynamoDB: the once-daily batch ingest (aws-lambdas/nfl/ingest/)
remains the only writer of anything durable, and normalize's status field
(scheduled/completed) stays exactly two-valued for every existing
consumer that already depends on that split (standings, backtesting,
etc.). This is a short-lived, UI-display-only cache in S3, refreshed on
its own schedule (scheduler-nfl-live-scores.tf, every 60s) and read back
by GET /nfl/live-scores (this same Lambda, see handler.py) -- the
"proper" event/score record still only ever gets written by tomorrow's
regular batch ingest run.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.normalize.espn import boxscore_to_player_game_stats
from library.parsing import parse_number

logger = logging.getLogger("nfl-live-scores")

LIVE_SCORES_CACHE_KEY = "nfl/cache/live-scores/latest.json"

# Same compound-stat key shape aws-lambdas/nfl/normalize/handler.py's own
# _COMPOUND_KEY_SPLITS uses for a completed event's box score -- duplicated
# here rather than imported since this is a different Lambda package/deploy
# unit (see this module's own docstring for why it never shares code with
# normalize), same convention every other constant in this file already
# follows. Must stay in sync with normalize/handler.py's own copy.
_COMPOUND_KEY_SPLITS: dict[str, tuple[str, str]] = {
    "completions/passingAttempts": ("completions", "passing_attempts"),
    "sacks-sackYardsLost": ("sacks_taken", "sack_yards_lost"),
    "fieldGoalsMade/fieldGoalAttempts": ("field_goals_made", "field_goal_attempts"),
    "extraPointsMade/extraPointAttempts": ("extra_points_made", "extra_point_attempts"),
}

# One ESPN summary/boxscore call per currently-live event, on top of the
# single scoreboard call every candidate already shares -- parallelized
# (RateLimiter still paces actual dispatch, see library/http/rate_limiter.py)
# so a Sunday with several games live at once doesn't pay each fetch's own
# latency serially against this Lambda's own timeout.
BOXSCORE_MAX_WORKERS = 10

# How early before a scheduled kickoff to start polling -- catches a game
# that goes live a few minutes early/on time without waiting for the
# scheduled kickoff_time to have technically arrived.
POLL_START_BEFORE_KICKOFF = timedelta(minutes=15)

# Hard safety cap so a data anomaly (a bad kickoff_time, or a game whose
# status never reaches completed) can't get polled forever -- no real NFL
# game runs anywhere close to this long after its own kickoff.
POLL_SAFETY_CAP_AFTER_KICKOFF = timedelta(hours=7)

# How stale the cache is allowed to look before a reader should treat it
# as "unknown" rather than trust it -- a few missed 60s ticks in a row
# (an ESPN hiccup, a Lambda error) shouldn't leave a confidently-wrong
# LIVE pill and score on screen indefinitely. Read by handler.py, not
# refresh() below -- refresh() always writes a fresh fetched_at itself.
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
    # ESPN's own timestamp shape ("...Z") -- Python 3.11+'s
    # datetime.fromisoformat handles the Z suffix directly, but the
    # explicit replace matches how the rest of this codebase already
    # parses the same field (library/features/nfl.py) rather than
    # depending on that being true of whatever Lambda runtime this ships
    # on.
    return datetime.fromisoformat(kickoff_time.replace("Z", "+00:00"))


def _candidate_events(storage, sport: str, now: datetime, already_completed: set[str]) -> list[dict]:
    """Events worth checking against ESPN's live scoreboard this cycle --
    not yet completed per our own once-daily batch ingest (which is what
    "scheduled" means here, not literally "hasn't started"), within the
    plausible live window, and not already confirmed completed by a PRIOR
    refresh cycle (already_completed) -- once this job itself has seen an
    event reach completed, there's no reason to keep spending an ESPN
    call re-confirming that every minute for the rest of the day."""
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
    """Best-effort entity_id -> stat_line for one currently-live event, from
    ESPN's own boxscore/summary endpoint -- unlike the once-a-day batch
    ingest's own box score fetch (which deliberately skips any event not
    yet marked completed, see aws-lambdas/nfl/ingest/handler.py), this one
    updates in near-real-time while the game is still being played. Empty
    on any fetch/parse failure rather than raising -- one bad event
    shouldn't cost every other live event its own score/stat refresh this
    tick."""
    try:
        summary = client.get_summary(event_id)
        stats_items, _ = boxscore_to_player_game_stats(summary, sport, _COMPOUND_KEY_SPLITS)
        return {item["entity_id"]: item["stat_line"] for item in stats_items}
    except Exception:
        logger.exception("Failed fetching live box score for event %s -- omitting player_stats this tick", event_id)
        return {}


def refresh(storage, s3, bucket: str, client, sport: str) -> dict:
    """Called on every LiveScoreRefresh tick (every 60s -- see
    scheduler-nfl-live-scores.tf). Cheap on every tick where nothing is
    actually in its live window: reads already-ingested events from
    DynamoDB (fast, no ESPN call), and only reaches out to ESPN at all
    once _candidate_events finds something worth checking."""
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

    # Boxscore fetch is its own ESPN call per event (unlike the score/status
    # above, which comes free for every candidate off the one scoreboard
    # call) -- only worth paying for an event ESPN itself confirms is
    # actually being played right now, not merely in the poll window (a
    # scheduled-but-not-yet-kicked-off or already-final candidate has
    # nothing live to show).
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
    """Called by GET /nfl/live-scores (handler.py). Returns {"events": {}}
    (empty, not an error) whenever there's nothing usable to serve --
    never cached yet, or stale enough (STALE_AFTER) that it shouldn't be
    trusted -- so callers never need to special-case "no live games
    right now" differently from "the cache looks unreliable right now"."""
    cache = _get_cache(s3, bucket)
    if cache is None:
        return {"events": {}}

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    if datetime.now(timezone.utc) - fetched_at > STALE_AFTER:
        logger.warning("Live-scores cache is stale (fetched_at=%s) -- serving empty", cache["fetched_at"])
        return {"events": {}}

    return {"events": cache.get("events", {})}
