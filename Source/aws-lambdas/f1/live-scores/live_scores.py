"""
Live-score cache for F1 races -- genuinely DIFFERENT data source from
every other part of F1's own pipeline: ingest/normalize/backfill/
feature-engineering are all Jolpica-sourced (library/http/f1.py), because
Jolpica has no live-timing data at all -- a round's own /results call
returns an empty Results list until the session is fully over (see
project-f1-onboarding memory). ESPN's own real F1 coverage DOES carry a
live per-session running order -- see library/http/f1_espn.py's own
docstring for the exact confirmed shape and the two endpoints that do
NOT work for F1.

Never writes to DynamoDB -- this is a short-lived, UI-display-only cache
in S3, refreshed on its own schedule (scheduler-f1-live-scores.tf) and
read back by GET /f1/live-scores (this same Lambda, see handler.py). Same
shape as PGA's own aws-lambdas/pga/live-scores/live_scores.py.

Two cross-provider joins this module owns, since ESPN's own id spaces
have no relationship to Jolpica's at all:

  1. DRIVER: ESPN's own athlete id (e.g. "5503") has no crosswalk to
     Jolpica's driverId (e.g. "russell") on either side. Matched by
     NORMALIZED NAME instead (_normalize_name) against the CURRENT
     roster's own real names, resolved from the most recently completed
     "field" race's own stored participants -- same "most recent field
     race = current lineup" idea aws-lambdas/f1/predict/live_features.py's
     own current_roster function already uses. Inherently a heuristic,
     not a guaranteed-unique key -- an unmatched ESPN competitor is
     logged and skipped, never crashes the whole refresh.

  2. EVENT: ESPN's own event name is sponsor-prefixed ("Pirelli Italian
     Grand Prix") and never matches Jolpica's own bare race_name
     ("Italian Grand Prix") exactly. Matched by calendar date instead --
     ESPN's own "Race" competition date and Jolpica's own event_date
     agree exactly for the same real race, since both sources describe
     the same real-world event. Only "Race" and "Sprint" competitions are
     ever joined -- FP1/FP2/FP3/Qual have no corresponding stored event
     of their own at all (qualifying is MERGED into the race event, see
     library/normalize/f1.py's merge_qualifying_into_event; this project
     has no live-qualifying display concept to feed).

Much simpler polling-window design than PGA's own: ESPN's full-season
scoreboard call already returns every competition's own real status in
ONE request, so there's no separate per-event candidate-discovery step --
this Lambda just fetches that one response every tick and reads
status.type.state directly off it, instead of first discovering which
tournaments are in range and then fetching each one's own leaderboard
separately the way PGA's live-scores has to.

A session's cache entry is kept fresh for as long as our own storage
still calls this event "scheduled" -- not just for a fixed buffer after
ESPN's own state flips to "post" (see refresh()'s own docstring for why
a fixed buffer isn't enough here: ingest only re-fetches Jolpica's real
results once a day, so the real gap between "race over" and "our own
stored result lands" can run up to ~24h, not the couple of minutes a
fixed post-race buffer was sized for).
"""
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.aws.account import get_account_id

logger = logging.getLogger("f1-live-scores")

LIVE_SCORES_CACHE_KEY = "f1/cache/live-scores/latest.json"

# 3x the poll cadence (scheduler-f1-live-scores.tf, 3 minutes) -- same
# "tolerate a couple of missed/late ticks" reasoning PGA's own STALE_AFTER
# uses.
STALE_AFTER = timedelta(minutes=10)

# Only these ESPN competition types have a corresponding stored event of
# our own at all -- see this module's own docstring.
_RACE_TYPE = "Race"
_SPRINT_TYPE = "Sprint"
_RELEVANT_SESSION_TYPES = {_RACE_TYPE: "field", _SPRINT_TYPE: "sprint"}


def _normalize_name(name: str) -> str:
    """Lowercase, strips accents/diacritics (NFKD decomposition + drop
    combining marks) and non-alphanumeric characters, collapses
    whitespace -- "Nico Hülkenberg" and "Nico Hulkenberg" both normalize
    to "nico hulkenberg". The only cross-reference available between
    ESPN's own athlete id space and Jolpica's driverId space this
    project's entities are keyed by -- see this module's own docstring."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def _current_roster_by_name(storage, sport: str) -> dict[str, str]:
    """{normalized_name: entity_id} for the current lineup."""
    completed_field = [e for e in storage.get_all_events(sport, status="completed") if e.get("event_type") == "field"]
    if not completed_field:
        return {}
    most_recent = max(completed_field, key=lambda e: e.get("event_date", ""))
    lookup = {}
    for participant in most_recent.get("participants", []):
        entity_id = participant["entity_id"]
        entity = storage.get_entity(sport, entity_id, "player")
        name = (entity or {}).get("name")
        if name:
            lookup[_normalize_name(name)] = entity_id
    return lookup


def _current_roster_by_last_name(roster_by_name: dict[str, str]) -> dict[str, str]:
    """{last_name: entity_id}, only for last names that are unique within
    the current roster -- a fallback for ESPN's own shorter display name
    (e.g. "Kimi Antonelli" for our own Jolpica-sourced "Andrea Kimi
    Antonelli") that never exact-matches _normalize_name's full-name
    output. Confirmed live 2026-09-13: this dropped that race's own
    winner out of the leaderboard for the whole race. A last name shared
    by two current drivers is deliberately left out rather than guessed
    at -- exact full-name matching already covers that case."""
    by_last_name: dict[str, list[str]] = {}
    for normalized_name, entity_id in roster_by_name.items():
        by_last_name.setdefault(normalized_name.rsplit(" ", 1)[-1], []).append(entity_id)
    return {last_name: ids[0] for last_name, ids in by_last_name.items() if len(ids) == 1}


def _event_ids_by_date_and_type(storage, sport: str) -> dict[tuple[str, str], str]:
    """{(event_date, event_type): event_id} for EVERY stored F1 event
    (both "field" and "sprint", any status) -- the join key back to
    ESPN's own competition date."""
    lookup = {}
    for event in storage.get_all_events(sport):
        event_date = event.get("event_date")
        event_type = event.get("event_type")
        if event_date and event_type in ("field", "sprint"):
            lookup[(event_date, event_type)] = event["event_id"]
    for event in storage.get_all_events(sport, status="scheduled"):
        event_date = event.get("event_date")
        event_type = event.get("event_type")
        if event_date and event_type in ("field", "sprint"):
            lookup.setdefault((event_date, event_type), event["event_id"])
    return lookup


def _get_cache(s3, bucket: str) -> dict | None:
    try:
        response = s3.get_object(Bucket=bucket, Key=LIVE_SCORES_CACHE_KEY, ExpectedBucketOwner=get_account_id())
        return json.loads(response["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return None  # cache miss or malformed entry -- treat as "nothing cached yet"


def _put_cache(s3, bucket: str, payload: dict) -> None:
    s3.put_object(
        Bucket=bucket, Key=LIVE_SCORES_CACHE_KEY, Body=json.dumps(payload), ContentType="application/json",
        ExpectedBucketOwner=get_account_id(),
    )


def _competition_participants(
    competition: dict, roster_by_name: dict[str, str], roster_by_last_name: dict[str, str],
) -> dict[str, dict]:
    participants = {}
    for competitor in competition.get("competitors", []):
        athlete = competitor.get("athlete") or {}
        full_name = athlete.get("fullName") or athlete.get("displayName")
        if not full_name:
            continue
        normalized = _normalize_name(full_name)
        entity_id = roster_by_name.get(normalized) or roster_by_last_name.get(normalized.rsplit(" ", 1)[-1])
        if entity_id is None:
            logger.info("No known F1 driver matches ESPN competitor name %r -- skipping", full_name)
            continue
        participants[entity_id] = {"order": competitor.get("order"), "winner": bool(competitor.get("winner", False))}
    return participants


def refresh(storage, s3, bucket: str, client, sport: str, season: int) -> dict:
    """Called on every LiveScoreRefresh tick (scheduler-f1-live-scores.tf).
    Always fetches the full-season scoreboard -- see this module's own
    docstring for why there's no cheaper candidate-discovery step to skip
    that call with, the way PGA's own refresh() has.

    A session's cache entry is kept up to date for as long as our own
    storage still has this event as "scheduled" -- NOT just for a fixed
    tail buffer after ESPN's own state flips to "post". Ingest only
    re-fetches Jolpica's real results once a day (the shared registry-
    driven orchestrator), so a race that just finished can go up to ~24h
    before normalize actually writes its real result and flips the
    stored event to "completed" (library/normalize/f1.py's own
    _event_item_from_race: "completed" if participants else "scheduled").
    A short fixed buffer here previously meant the
    leaderboard reverted to looking pre-race for that whole gap, ~1h
    after the checkered flag fell -- this refetches ESPN's own already-
    final classification (order/winner) every tick regardless of how long
    ago the session ended, right up until our own storage catches up on
    its own. Once it does, this event drops out of scheduled_event_ids
    and out of events_out with it -- the frontend's own real result
    (from the stored event itself) takes over from there, so this cache
    doesn't need to keep serving ESPN's copy any longer."""
    now = datetime.now(timezone.utc)

    scoreboard = client.get_scoreboard(season)
    espn_events = scoreboard.get("events", [])
    if not espn_events:
        logger.info("Empty F1 scoreboard response -- skipping")
        return {"polled": 0}

    roster_by_name = _current_roster_by_name(storage, sport)
    roster_by_last_name = _current_roster_by_last_name(roster_by_name)
    event_ids = _event_ids_by_date_and_type(storage, sport)
    scheduled_event_ids = {event["event_id"] for event in storage.get_all_events(sport, status="scheduled")}

    events_out = {}
    for espn_event in espn_events:
        for competition in espn_event.get("competitions", []):
            _apply_competition_state(
                competition, espn_event.get("name"), event_ids, scheduled_event_ids,
                roster_by_name, roster_by_last_name, events_out,
            )

    _put_cache(s3, bucket, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed F1 live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


def _apply_competition_state(
    competition: dict, race_name: str | None, event_ids: dict, scheduled_event_ids: set[str],
    roster_by_name: dict, roster_by_last_name: dict, events_out: dict,
) -> None:
    """Applies one ESPN competition's own current state into events_out in
    place, if it's a session type this project tracks and joins to a real
    stored event -- see refresh's own docstring for the join/gating
    rules."""
    session_type = (competition.get("type") or {}).get("abbreviation")
    our_event_type = _RELEVANT_SESSION_TYPES.get(session_type)
    if our_event_type is None:
        return  # FP1/FP2/FP3/Qual -- no stored event of our own to join against

    competition_date = (competition.get("date") or "")[:10]
    our_event_id = event_ids.get((competition_date, our_event_type))
    if our_event_id is None:
        return  # not yet backfilled/normalized into our own storage

    status = (competition.get("status") or {}).get("type", {})
    state = status.get("state")
    if state == "pre":
        return  # hasn't started yet -- nothing live to show
    if state != "in" and our_event_id not in scheduled_event_ids:
        return  # our own storage already has the real result

    events_out[our_event_id] = {
        "event_type": our_event_type,
        "status": status.get("name"),
        "state": state,
        "race_name": race_name,
        "participants": _competition_participants(competition, roster_by_name, roster_by_last_name),
    }


def get_live_scores(s3, bucket: str) -> dict:
    """Called by GET /f1/live-scores (handler.py). Returns an empty
    events dict rather than an error on a cache miss or stale cache."""
    cache = _get_cache(s3, bucket)
    if cache is None:
        return {"events": {}}

    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    if datetime.now(timezone.utc) - fetched_at > STALE_AFTER:
        logger.warning("F1 live-scores cache is stale (fetched_at=%s) -- serving empty", cache["fetched_at"])
        return {"events": {}}

    return {"events": cache.get("events", {})}
