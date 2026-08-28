"""
Live leaderboard-snapshot cache for PGA tournaments. Never writes to
DynamoDB -- this is a short-lived, UI-display-only cache in S3, refreshed
on its own schedule (scheduler-pga-live-scores.tf, every 5 minutes) and
read back by GET /pga/live-scores (this same Lambda, see handler.py).

Deliberately NOT a port of NBA/NFL's live-scores shape. PGA has no
hole-level granularity and no live-boxscore-equivalent endpoint anywhere
in this codebase (PGAClient.get_leaderboard is the only per-tournament
data source, and it returns round-level totals, not sub-round state) --
see project-pga-onboarding memory. So this is framed as a fresher
"last-updated leaderboard snapshot", not literal real-time scores, and
polls on a flat 5-minute cadence during a tournament's active window
rather than NBA/NFL's tight kickoff-relative one.

Poll-window design: gated on library/normalize/pga.py's own
`next_tee_time` field -- the earliest known upcoming tee time across
still-in-the-tournament competitors, taken from ESPN's own
status.teeTime (confirmed live 2026-08-27/28 on the real in-progress TOUR
Championship). An EARLIER version of this module tried to derive a
"tournament start time" from the leaderboard event's own top-level `date`
field instead; that field turned out to be a static midnight-UTC
placeholder on the real response, not a tee time at all, and was
discarded once found -- see next_tee_time's own docstring. Because
next_tee_time is refreshed by BOTH the daily pga-normalize path and this
Lambda's own poll, a day's real tee times are typically already sitting
in DynamoDB (from the prior day's ingest or this Lambda's own end-of-day
tick) before the scheduler ever needs to gate on them that day -- no
dedicated "discover tee times" call needed most of the time. The only
remaining "wasted" calls are: (a) once per tournament, before ANY tee
time has ever been published yet (falls back to polling the whole
calendar day within [event_date, end_date] until one is learned), and
(b) the END_BUFFER tail below, which exists specifically to catch a
slow-finishing last group without a stored end-of-round timestamp.

"Last group has finished their round" has no direct clock signal either
-- it's read off competitor status. _is_active is deliberately written
as "status is not one of the confirmed-terminal set", not an equality
check against a guessed in-progress status string -- an in-progress
status has never actually been observed live in this codebase yet (see
library/normalize/pga.py's own _STATUS_MAP docstring), since no live poll
has ever run against this data before this Lambda.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.normalize.pga import is_flat_stroke_play, leaderboard_event_to_event_item

logger = logging.getLogger("pga-live-scores")

LIVE_SCORES_CACHE_KEY = "pga/cache/live-scores/latest.json"

# 3x the 5-minute poll cadence -- tolerates one missed/late tick without
# a reader treating a still-fresh cache as unknown.
STALE_AFTER = timedelta(minutes=15)

# Start polling 1h before the earliest known upcoming tee time; keep
# polling 1h after every still-active golfer's status goes non-active, to
# catch a slow-finishing last group.
START_BUFFER = timedelta(hours=1)
END_BUFFER = timedelta(hours=1)

# Confirmed-terminal PGA competitor statuses (library/normalize/pga.py's
# map_status vocabulary) -- anything else (including an unconfirmed
# in-progress status ESPN may use, and "scheduled" before a golfer has
# teed off) is treated as "still needs polling", per this module's own
# docstring on why an equality check against a guessed string is wrong.
_TERMINAL_STATUSES = {"finished", "cut", "made_cut_did_not_finish", "withdrawn"}


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


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _in_tournament_range(event: dict, day: date) -> bool:
    """Coarse date-level gate -- is `day` anywhere within this
    tournament's own [event_date, end_date]? A missing/unparseable
    event_date excludes the event entirely (nothing to anchor to); a
    missing/unparseable end_date falls back to event_date itself
    (single-day window) rather than polling forever, same "err toward
    still poll" precedent as schedule-sync's own _in_refresh_window for
    the cases that DO have a real value to fall back to."""
    event_date = _parse_date(event.get("event_date"))
    if event_date is None:
        return False
    end_date = _parse_date(event.get("end_date")) or event_date
    return event_date <= day <= end_date


def _is_active(event_item: dict) -> bool:
    """True if any participant's result.status is not in the confirmed-
    terminal set -- see module docstring. An event with zero participants
    (e.g. field not posted yet) counts as active -- nothing to poll
    toward completion, but also nothing confirming the day is done."""
    participants = event_item.get("participants", [])
    if not participants:
        return True
    return any((p.get("result") or {}).get("status") not in _TERMINAL_STATUSES for p in participants)


def _candidate_events(storage, sport: str, now: datetime, last_active_at: dict[str, str]) -> list[dict]:
    """storage.get_all_events(sport, status='scheduled') returns every
    future tournament on the season calendar (~45-51, schedule-sync seeds
    months ahead) -- this date-range filter is load-bearing, not defense-
    in-depth. Within that range, a candidate is included if: its own
    next_tee_time is within START_BUFFER of `now` (or already past), OR
    next_tee_time isn't known yet at all (poll to help discover it), OR
    the previous cache still marked this event active within the last
    END_BUFFER (the post-finish tail, implemented without a dedicated
    stored end-of-round time)."""
    today = now.date()
    candidates = []
    for event in storage.get_all_events(sport, status="scheduled"):
        if not _in_tournament_range(event, today):
            continue
        event_id = event["event_id"]

        next_tee_time = _parse_datetime(event.get("next_tee_time"))
        tee_time_due = next_tee_time is None or now >= next_tee_time - START_BUFFER

        last_active = _parse_datetime(last_active_at.get(event_id))
        in_tail = last_active is not None and now <= last_active + END_BUFFER

        if tee_time_due or in_tail:
            candidates.append(event)
    return candidates


def refresh(storage, s3, bucket: str, client, sport: str) -> dict:
    """Called on every LiveScoreRefresh tick (scheduler-pga-live-scores.tf,
    every 5 minutes, unconditional). Cheap on a tick with nothing in its
    poll window: the DynamoDB read is local, and _candidate_events'
    window filter (using the real next_tee_time, not a guessed clock
    window) skips the ESPN call entirely outside tournament play hours
    once a day's tee times are known."""
    now = datetime.now(timezone.utc)

    previous = _get_cache(s3, bucket) or {}
    previous_events = previous.get("events", {})
    last_active_at = {
        event_id: state["last_active_at"]
        for event_id, state in previous_events.items() if state.get("last_active_at")
    }

    candidates = _candidate_events(storage, sport, now, last_active_at)
    if not candidates:
        logger.info("No tournaments in a live-poll window -- skipping ESPN call")
        return {"polled": 0}

    events_out = {}
    for event in candidates:
        event_id = event["event_id"]
        try:
            leaderboard = client.get_leaderboard(event_id)
            espn_events = leaderboard.get("events", [])
            if not espn_events:
                logger.warning("Empty leaderboard response for event %s -- skipping", event_id)
                continue
            espn_event = espn_events[0]
            if not is_flat_stroke_play(espn_event):
                logger.info("Event %s is not stroke-play -- skipping (live-scores is field-only for now)", event_id)
                continue

            item = leaderboard_event_to_event_item(espn_event, sport)
            active = _is_active(item)
            state = {
                "status": item["status"],
                "tournament_name": item["tournament_name"],
                "participants": {p["entity_id"]: p["result"] for p in item["participants"]},
            }
            if active:
                state["last_active_at"] = now.isoformat()
            elif event_id in last_active_at:
                state["last_active_at"] = last_active_at[event_id]  # preserve through the end-buffer tail
            events_out[event_id] = state
        except Exception:
            logger.exception("Failed live-polling event %s", event_id)
            continue  # one bad tournament shouldn't cost every other candidate its own refresh

    _put_cache(s3, bucket, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


def get_live_scores(s3, bucket: str) -> dict:
    """Called by GET /pga/live-scores (handler.py). Returns an empty
    events dict rather than an error on a cache miss or stale cache."""
    cache = _get_cache(s3, bucket)
    if cache is None:
        return {"events": {}}

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    if datetime.now(timezone.utc) - fetched_at > STALE_AFTER:
        logger.warning("Live-scores cache is stale (fetched_at=%s) -- serving empty", cache["fetched_at"])
        return {"events": {}}

    return {"events": cache.get("events", {})}
