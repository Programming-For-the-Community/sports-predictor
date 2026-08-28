"""
Live leaderboard-snapshot cache for PGA tournaments -- field (stroke-play)
events, and (as of this pass) match_play/cup events (Ryder Cup/Presidents
Cup). Never writes to DynamoDB -- this is a short-lived, UI-display-only
cache in S3, refreshed on its own schedule (scheduler-pga-live-scores.tf,
every 1 minute) and read back by GET /pga/live-scores (this same Lambda,
see handler.py).

Deliberately NOT a port of NBA/NFL's live-scores shape. PGA has no
hole-level granularity and no live-boxscore-equivalent endpoint anywhere
in this codebase (PGAClient.get_leaderboard is the only per-tournament
data source, and it returns round-level/match-level totals, not
sub-round/sub-match state) -- see project-pga-onboarding memory. So this
is framed as a fresher "last-updated leaderboard snapshot", not literal
real-time scores, and polls on a flat 1-minute cadence during a
tournament's active window rather than NBA/NFL's tight kickoff-relative
one.

Poll-window design (field events): gated on library/normalize/pga.py's
own `next_tee_time` field -- the earliest known upcoming tee time across
still-in-the-tournament competitors, taken from ESPN's own
status.teeTime (confirmed live 2026-08-27/28 on the real in-progress TOUR
Championship). An EARLIER version of this module tried to derive a
"tournament start time" from the leaderboard event's own top-level `date`
field instead; that field turned out to be a static midnight-UTC
placeholder on the real response, not a tee time at all, and was
discarded once found -- see next_tee_time's own docstring. Because
next_tee_time is refreshed by BOTH the daily pga-normalize path and this
Lambda's own poll, a day's real tee times are typically already sitting
in DynamoDB before the scheduler ever needs to gate on them that day.

Poll-window design (match_play/cup events, added this pass): genuinely
different shape from field events, not a variant of the same logic --
library/normalize/pga_matchplay.py's own `match_time` field (confirmed
live against a real historical Presidents Cup: each match carries its
own real, distinct start timestamp, e.g. two same-session matches 12
minutes apart -- NOT the same fake-placeholder problem field's top-level
`date` had) is the direct per-match signal, no derivation trick needed.
The structural wrinkle unique to this event shape: a match_play event's
own `event_id` is SYNTHESIZED (f"{tournament_event_id}-match-{match_id}"),
not a real ESPN id get_leaderboard() accepts -- refresh() fetches by the
real tournament id (a match row's own `parent_event_id`, or a cup row's
own `event_id`, which already IS the real tournament id) and re-derives
every match+cup item from that ONE response, mirroring exactly how
pga-normalize itself already works. Rare in practice -- Ryder Cup and
Presidents Cup alternate annually (~4 days/year combined), and
WGC-Dell Technologies Match Play (the only individual-match-play format)
was discontinued after 2023.

"Last group/match has finished" has no direct clock signal for any event
type -- it's read off competitor/match status. _is_active is deliberately
written as "status is not one of the confirmed-terminal set", not an
equality check against a guessed in-progress status string -- "in_progress"
IS now a confirmed real status (library/normalize/pga.py's own
_STATUS_MAP), but this still isn't written as an equality check against
it: anything ESPN adds in the future that isn't in the confirmed-terminal
set below should also keep polling, not just this one known value.
A cup event's own participants carry no status field at all (their
result is {points, won, halved}) -- _is_active branches on the cup
item's own top-level status field instead for that event_type.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.normalize.pga import is_flat_stroke_play, leaderboard_event_to_event_item
from library.normalize.pga_matchplay import (
    is_supported_match_play,
    leaderboard_event_to_cup_event_item,
    leaderboard_event_to_match_event_items,
)

logger = logging.getLogger("pga-live-scores")

LIVE_SCORES_CACHE_KEY = "pga/cache/live-scores/latest.json"

# 3x the poll cadence (scheduler-pga-live-scores.tf, 1 minute as of
# 2026-08-28, bumped from 5) -- tolerates a couple of missed/late ticks
# without a reader treating a still-fresh cache as unknown.
STALE_AFTER = timedelta(minutes=3)

# Start polling 1h before the earliest known upcoming tee time/match time;
# keep polling 1h after every still-active golfer/match's status goes
# non-active, to catch a slow-finishing last group/match.
START_BUFFER = timedelta(hours=1)
END_BUFFER = timedelta(hours=1)

# How long after a match's own match_time that timestamp alone still
# counts as "due" for _match_cup_tournament_ids -- see that function's
# own comment. A generous cap no real Ryder/Presidents Cup match
# (foursomes/fourball/singles, all single-round) approaches.
MATCH_TIME_SAFETY_CAP = timedelta(hours=6)

# Confirmed-terminal PGA competitor statuses (library/normalize/pga.py's
# map_status vocabulary, shared by both golfer and match-play results) --
# anything else (including an unconfirmed in-progress status ESPN may
# use, and "scheduled" before play has started) is treated as "still
# needs polling", per this module's own docstring on why an equality
# check against a guessed string is wrong.
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
    """True if this event still needs polling toward completion. A "cup"
    item's own participants carry no status field at all (result is
    {points, won, halved}) -- checked via the item's own top-level status
    (event_status()'s "scheduled"/"completed" binary) instead. "field"/
    "match_play" check per-participant status against the confirmed-
    terminal set; an event with zero participants (e.g. field not posted
    yet) counts as active -- nothing to poll toward completion, but also
    nothing confirming it's done."""
    if event_item.get("event_type") == "cup":
        return event_item.get("status") != "completed"
    participants = event_item.get("participants", [])
    if not participants:
        return True
    return any((p.get("result") or {}).get("status") not in _TERMINAL_STATUSES for p in participants)


def _field_candidates(storage, sport: str, now: datetime, last_active_at: dict[str, str]) -> list[dict]:
    """Field-event candidates -- unchanged from the original field-only
    design. Returns the stored event rows themselves (each already
    carries its own real ESPN event_id, usable directly against
    get_leaderboard)."""
    today = now.date()
    candidates = []
    for event in storage.get_all_events(sport, status="scheduled"):
        if event.get("event_type") != "field" or not _in_tournament_range(event, today):
            continue
        event_id = event["event_id"]

        next_tee_time = _parse_datetime(event.get("next_tee_time"))
        tee_time_due = next_tee_time is None or now >= next_tee_time - START_BUFFER

        last_active = _parse_datetime(last_active_at.get(event_id))
        in_tail = last_active is not None and now <= last_active + END_BUFFER

        if tee_time_due or in_tail:
            candidates.append(event)
    return candidates


def _match_cup_tournament_ids(storage, sport: str, now: datetime, last_active_at: dict[str, str]) -> list[str]:
    """Real tournament ids (never a synthesized match event_id) worth a
    get_leaderboard call this tick. Groups every stored match_play row by
    its own parent_event_id (the real tournament id) and every stored cup
    row by its own event_id (which already IS the real tournament id),
    then includes a tournament id if ANY of its rows: has a match_time
    within START_BUFFER of now (or already past); has no known match_time
    at all yet but falls within _in_tournament_range (bootstrap fallback,
    err toward polling -- a match row existing at all with no time set
    yet hasn't been observed live, but the same "unparseable/missing --
    still poll" precedent applies); or has its OWN event_id still inside
    the END_BUFFER tail (reuses the same per-event-id last_active_at
    bookkeeping refresh() already builds for field events -- no separate
    tournament-level tracking needed)."""
    today = now.date()
    rows_by_tournament: dict[str, list[dict]] = {}
    for event in storage.get_all_events(sport, status="scheduled"):
        event_type = event.get("event_type")
        if event_type == "match_play":
            tournament_id = event.get("parent_event_id")
        elif event_type == "cup":
            tournament_id = event.get("event_id")
        else:
            continue
        if tournament_id is None:
            continue
        rows_by_tournament.setdefault(tournament_id, []).append(event)

    tournament_ids = []
    for tournament_id, rows in rows_by_tournament.items():
        match_rows = [r for r in rows if r.get("event_type") == "match_play"]
        known_match_times = [_parse_datetime(r.get("match_time")) for r in match_rows]
        known_match_times = [t for t in known_match_times if t is not None]

        # Unlike field's next_tee_time (which rolls forward to the next
        # round once the current one's done, self-correcting), a match's
        # own match_time is a one-shot timestamp that never moves once
        # the match is over -- MATCH_TIME_SAFETY_CAP bounds how long
        # after it this alone still counts as "due" (same reasoning as
        # NBA live-scores' own POLL_SAFETY_CAP_AFTER_KICKOFF: a generous
        # cap no real match approaches). Beyond the cap, only the
        # last_active_at tail below keeps a still-active tournament going.
        time_due = any(t - START_BUFFER <= now <= t + MATCH_TIME_SAFETY_CAP for t in known_match_times)
        no_time_known_yet = bool(match_rows) and not known_match_times
        range_fallback_due = no_time_known_yet and any(_in_tournament_range(r, today) for r in rows)

        in_tail = any(
            (last_active := _parse_datetime(last_active_at.get(r["event_id"]))) is not None
            and now <= last_active + END_BUFFER
            for r in rows
        )

        if time_due or range_fallback_due or in_tail:
            tournament_ids.append(tournament_id)
    return tournament_ids


def refresh(storage, s3, bucket: str, client, sport: str) -> dict:
    """Called on every LiveScoreRefresh tick (scheduler-pga-live-scores.tf,
    every 1 minute, unconditional). Cheap on a tick with nothing in its
    poll window: the DynamoDB read is local, and the candidate filters
    (using real next_tee_time/match_time signals, not a guessed clock
    window) skip the ESPN call entirely outside tournament play hours
    once a day's tee/match times are known."""
    now = datetime.now(timezone.utc)

    previous = _get_cache(s3, bucket) or {}
    previous_events = previous.get("events", {})
    last_active_at = {
        event_id: state["last_active_at"]
        for event_id, state in previous_events.items() if state.get("last_active_at")
    }

    field_candidates = _field_candidates(storage, sport, now, last_active_at)
    match_cup_candidates = _match_cup_tournament_ids(storage, sport, now, last_active_at)
    if not field_candidates and not match_cup_candidates:
        logger.info("No tournaments in a live-poll window -- skipping ESPN call")
        return {"polled": 0}

    events_out = {}

    for event in field_candidates:
        event_id = event["event_id"]
        try:
            leaderboard = client.get_leaderboard(event_id)
            espn_events = leaderboard.get("events", [])
            if not espn_events:
                logger.warning("Empty leaderboard response for event %s -- skipping", event_id)
                continue
            espn_event = espn_events[0]
            if not is_flat_stroke_play(espn_event):
                logger.info("Event %s is no longer stroke-play -- skipping this tick", event_id)
                continue

            item = leaderboard_event_to_event_item(espn_event, sport)
            _record_item(events_out, event_id, item, last_active_at, now)
        except Exception:
            logger.exception("Failed live-polling field event %s", event_id)
            continue  # one bad tournament shouldn't cost every other candidate its own refresh

    for tournament_id in match_cup_candidates:
        try:
            leaderboard = client.get_leaderboard(tournament_id)
            espn_events = leaderboard.get("events", [])
            if not espn_events:
                logger.warning("Empty leaderboard response for tournament %s -- skipping", tournament_id)
                continue
            espn_event = espn_events[0]
            if not is_supported_match_play(espn_event):
                logger.info("Tournament %s is no longer supported match play -- skipping this tick", tournament_id)
                continue

            for match_item in leaderboard_event_to_match_event_items(espn_event, sport):
                _record_item(events_out, match_item["event_id"], match_item, last_active_at, now)

            cup_item = leaderboard_event_to_cup_event_item(espn_event, sport)
            if cup_item is not None:
                _record_item(events_out, cup_item["event_id"], cup_item, last_active_at, now)
        except Exception:
            logger.exception("Failed live-polling match/cup tournament %s", tournament_id)
            continue

    _put_cache(s3, bucket, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


def _record_item(events_out: dict, event_id: str, item: dict, last_active_at: dict[str, str], now: datetime) -> None:
    """Shared per-item cache-entry builder for all three event_types --
    field/match_play carry per-participant results, cup carries its own
    top-level status. event_type is included explicitly so a consumer
    never has to guess the participant shape from context."""
    active = _is_active(item)
    state = {
        "event_type": item["event_type"],
        "status": item["status"],
        "tournament_name": item.get("tournament_name"),
        "participants": {p["entity_id"]: p["result"] for p in item.get("participants", [])},
    }
    if active:
        state["last_active_at"] = now.isoformat()
    elif event_id in last_active_at:
        state["last_active_at"] = last_active_at[event_id]  # preserve through the end-buffer tail
    events_out[event_id] = state


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
