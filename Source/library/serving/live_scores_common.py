"""
Shared live score/status cache logic for a UTC-scoreboard-per-day, ESPN-
sourced head-to-head sport (nfl/nba/ncaafb/ncaambb -- confirmed byte-for-
byte identical across all 4 before sharing here). Never writes to
DynamoDB -- this is a short-lived, UI-display-only cache in S3, refreshed
on each sport's own EventBridge schedule and read back by that sport's own
GET /{sport}/live-scores.

PGA and F1 are NOT consolidated here -- confirmed genuinely different
shapes (PGA has no boxscore-equivalent endpoint and polls on tee/match-time
signals; F1 does a cross-provider ESPN<->Jolpica join by name/date), see
each sport's own live_scores.py docstring.

Each per-sport live_scores.py keeps its own `LIVE_SCORES_CACHE_KEY`/
`_COMPOUND_KEY_SPLITS`/`BOXSCORE_MAX_WORKERS` config plus thin `refresh`/
`get_live_scores`/`_live_player_stats`/`_get_cache`/`_put_cache` wrappers
that pass that config in here -- `refresh`/`get_live_scores` stay real,
independently patchable module-level functions on each sport's own module
(handler.py tests patch them by attribute), rather than being collapsed
into one shared callable.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.aws.account import get_account_id
from library.http.espn import espn_scoreboard_date
from library.normalize.espn import boxscore_to_player_game_stats
from library.parsing import parse_number

logger = logging.getLogger("library.serving.live_scores_common")

# How early before a scheduled kickoff to start polling -- catches a game
# that goes live a few minutes early/on time without waiting for the
# scheduled kickoff_time to have technically arrived.
POLL_START_BEFORE_KICKOFF = timedelta(minutes=15)

# Hard safety cap so a data anomaly (a bad kickoff_time, or a game whose
# status never reaches completed) can't get polled forever -- no real game
# in any of these sports runs anywhere close to this long after its own
# kickoff/tip-off.
POLL_SAFETY_CAP_AFTER_KICKOFF = timedelta(hours=7)

# How stale the cache is allowed to look before a reader should treat it
# as "unknown" rather than trust it. Read by get_live_scores below.
STALE_AFTER = timedelta(minutes=5)


def get_cache(s3, bucket: str, cache_key: str) -> dict | None:
    try:
        response = s3.get_object(Bucket=bucket, Key=cache_key, ExpectedBucketOwner=get_account_id())
        return json.loads(response["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return None  # cache miss or malformed entry -- treat as "nothing cached yet"


def put_cache(s3, bucket: str, cache_key: str, payload: dict) -> None:
    s3.put_object(
        Bucket=bucket, Key=cache_key,
        Body=json.dumps(payload), ContentType="application/json",
        ExpectedBucketOwner=get_account_id(),
    )


def parse_kickoff(kickoff_time: str) -> datetime:
    # ESPN's own timestamp shape ("...Z").
    return datetime.fromisoformat(kickoff_time.replace("Z", "+00:00"))


def candidate_events(scheduled_events: list[dict], now: datetime, already_completed: set[str]) -> list[dict]:
    """Events worth checking against ESPN's live scoreboard this cycle --
    status "scheduled" per the once-daily batch ingest (not literally
    "hasn't started"), within the plausible live window, and not already
    confirmed completed by a prior refresh cycle (already_completed)."""
    candidates = []
    for event in scheduled_events:
        event_id = event["event_id"]
        if event_id in already_completed:
            continue
        kickoff_time = event.get("kickoff_time")
        if kickoff_time is None:
            continue
        kickoff = parse_kickoff(kickoff_time)
        if kickoff - POLL_START_BEFORE_KICKOFF <= now <= kickoff + POLL_SAFETY_CAP_AFTER_KICKOFF:
            candidates.append(event)
    return candidates


def extract_live_state(espn_event: dict) -> dict:
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


def live_player_stats(client, sport: str, event_id: str, compound_key_splits: dict[str, tuple[str, str]]) -> dict[str, dict]:
    """Best-effort entity_id -> stat_line for one currently-live event,
    from ESPN's own boxscore/summary endpoint. Empty on any fetch/parse
    failure rather than raising, so one bad event doesn't cost every
    other live event its own score/stat refresh this tick."""
    try:
        summary = client.get_summary(event_id)
        stats_items, _ = boxscore_to_player_game_stats(summary, sport, compound_key_splits)
        return {item["entity_id"]: item["stat_line"] for item in stats_items}
    except Exception:
        logger.exception("Failed fetching live box score for event %s -- omitting player_stats this tick", event_id)
        return {}


def refresh(
    storage, s3, bucket: str, client, sport: str, cache_key: str,
    compound_key_splits: dict[str, tuple[str, str]], boxscore_max_workers: int = 10,
) -> dict:
    """Called on every LiveScoreRefresh tick. Reads already-ingested events
    from DynamoDB and only reaches out to ESPN once candidate_events finds
    something worth checking.

    A completed event's cached state is carried forward here past its own
    active poll window, not just left to fall out of candidates -- ingest
    only re-fetches this sport on its own schedule, so DynamoDB's own event
    status can lag the real result by up to 24h. Until that catches up
    (this event drops out of get_all_events(status="scheduled") on its own
    once it does), this cache is the only place the frontend can still get
    the game's final score/stats from -- without carrying it forward,
    "completed" state was being overwritten out of the cache on the very
    next tick, ~60s after the game actually ended, and the event reverted
    to looking unplayed."""
    now = datetime.now(timezone.utc)

    previous = get_cache(s3, bucket, cache_key) or {}
    previous_events = previous.get("events", {})
    already_completed = {event_id for event_id, state in previous_events.items() if state.get("completed")}

    scheduled_events = storage.get_all_events(sport, status="scheduled")
    scheduled_event_ids = {event["event_id"] for event in scheduled_events}
    carried_over = _carried_over_events(previous_events, already_completed, scheduled_event_ids)

    candidates = candidate_events(scheduled_events, now, already_completed)
    if not candidates:
        if carried_over:
            put_cache(s3, bucket, cache_key, {"fetched_at": now.isoformat(), "events": carried_over})
        else:
            logger.info("No events in a live-poll window -- skipping ESPN call")
        return {"polled": len(carried_over)}

    scoreboard = client.get_scoreboard_for_date(espn_scoreboard_date(now))
    espn_events_by_id = {e["id"]: e for e in scoreboard.get("events", [])}

    events_out = dict(carried_over)
    live_event_ids = _apply_scoreboard_states(candidates, espn_events_by_id, previous_events, events_out)
    _attach_live_player_stats(live_event_ids, client, sport, compound_key_splits, boxscore_max_workers, events_out)

    put_cache(s3, bucket, cache_key, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


def _carried_over_events(previous_events: dict, already_completed: set[str], scheduled_event_ids: set[str]) -> dict:
    return {event_id: previous_events[event_id] for event_id in already_completed if event_id in scheduled_event_ids}


def _apply_scoreboard_states(
    candidates: list[dict], espn_events_by_id: dict, previous_events: dict, events_out: dict,
) -> list[str]:
    """Applies each candidate's own current ESPN state into events_out (in
    place), returning the subset of event_ids ESPN confirms are actually
    live right now (worth a boxscore fetch)."""
    live_event_ids = []
    for event in candidates:
        espn_event = espn_events_by_id.get(event["event_id"])
        if espn_event is None:
            logger.warning("Candidate event %s not found in today's ESPN scoreboard -- skipping", event["event_id"])
            continue
        state = extract_live_state(espn_event)
        if state["completed"] and not state["live"]:
            # The tick a game finishes, ESPN's own state is no longer
            # "in" -- this event won't land in live_event_ids below, so
            # it gets no fresh boxscore fetch this cycle. Carry forward
            # its last-fetched player_stats rather than silently losing
            # the final box score the moment the game ends: this state
            # dict is what `carried_over` copies forward on every future
            # tick once already_completed picks it up next cycle, so this
            # transition tick is the only chance to attach it at all.
            previous_state = previous_events.get(event["event_id"])
            if previous_state and previous_state.get("player_stats"):
                state["player_stats"] = previous_state["player_stats"]
        events_out[event["event_id"]] = state
        if state["live"]:
            live_event_ids.append(event["event_id"])
    return live_event_ids


def _attach_live_player_stats(
    live_event_ids: list[str], client, sport: str, compound_key_splits: dict[str, tuple[str, str]],
    boxscore_max_workers: int, events_out: dict,
) -> None:
    """Fetches and attaches each live event's own boxscore player_stats
    into events_out in place -- one ESPN call per event, only for events
    ESPN confirms are actually being played right now."""
    if not live_event_ids:
        return
    with ThreadPoolExecutor(max_workers=min(len(live_event_ids), boxscore_max_workers)) as executor:
        player_stats_by_event = dict(zip(
            live_event_ids,
            executor.map(lambda event_id: live_player_stats(client, sport, event_id, compound_key_splits), live_event_ids),
        ))
    for event_id, player_stats in player_stats_by_event.items():
        events_out[event_id]["player_stats"] = player_stats


def get_live_scores(s3, bucket: str, cache_key: str) -> dict:
    """Called by a sport's own GET /{sport}/live-scores. Returns
    {"events": {}} (empty, not an error) whenever there's nothing usable
    to serve -- never cached yet, or stale enough (STALE_AFTER) that it
    shouldn't be trusted."""
    cache = get_cache(s3, bucket, cache_key)
    if cache is None:
        return {"events": {}}

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    if datetime.now(timezone.utc) - fetched_at > STALE_AFTER:
        logger.warning("Live-scores cache is stale (fetched_at=%s) -- serving empty", cache["fetched_at"])
        return {"events": {}}

    return {"events": cache.get("events", {})}
