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
     ("Italian Grand Prix") exactly. Matched by CALENDAR DATE instead --
     confirmed live 2026-08-31 that ESPN's own "Race" competition date
     and Jolpica's own event_date agree EXACTLY for the same real race
     (2026 Australian GP, both "2026-03-08"), since both sources describe
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
"""
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

logger = logging.getLogger("f1-live-scores")

LIVE_SCORES_CACHE_KEY = "f1/cache/live-scores/latest.json"

# 3x the poll cadence (scheduler-f1-live-scores.tf, 3 minutes) -- same
# "tolerate a couple of missed/late ticks" reasoning PGA's own STALE_AFTER
# uses.
STALE_AFTER = timedelta(minutes=10)

# Keep a just-finished race's own final live state visible for a while
# after ESPN itself flips state to "post" -- same tail-buffer idea PGA's
# own END_BUFFER uses, so the UI doesn't lose the live view the instant
# the checkered flag falls.
END_BUFFER = timedelta(hours=1)

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
        response = s3.get_object(Bucket=bucket, Key=LIVE_SCORES_CACHE_KEY)
        return json.loads(response["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return None  # cache miss or malformed entry -- treat as "nothing cached yet"


def _put_cache(s3, bucket: str, payload: dict) -> None:
    s3.put_object(Bucket=bucket, Key=LIVE_SCORES_CACHE_KEY, Body=json.dumps(payload), ContentType="application/json")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _competition_participants(competition: dict, roster_by_name: dict[str, str]) -> dict[str, dict]:
    participants = {}
    for competitor in competition.get("competitors", []):
        athlete = competitor.get("athlete") or {}
        full_name = athlete.get("fullName") or athlete.get("displayName")
        if not full_name:
            continue
        entity_id = roster_by_name.get(_normalize_name(full_name))
        if entity_id is None:
            logger.info("No known F1 driver matches ESPN competitor name %r -- skipping", full_name)
            continue
        participants[entity_id] = {"order": competitor.get("order"), "winner": bool(competitor.get("winner", False))}
    return participants


def refresh(storage, s3, bucket: str, client, sport: str, season: int) -> dict:
    """Called on every LiveScoreRefresh tick (scheduler-f1-live-scores.tf).
    Always fetches the full-season scoreboard -- see this module's own
    docstring for why there's no cheaper candidate-discovery step to skip
    that call with, the way PGA's own refresh() has."""
    now = datetime.now(timezone.utc)

    previous = _get_cache(s3, bucket) or {}
    previous_events = previous.get("events", {})
    last_active_at = {
        event_id: state["last_active_at"] for event_id, state in previous_events.items() if state.get("last_active_at")
    }

    scoreboard = client.get_scoreboard(season)
    espn_events = scoreboard.get("events", [])
    if not espn_events:
        logger.info("Empty F1 scoreboard response -- skipping")
        return {"polled": 0}

    roster_by_name = _current_roster_by_name(storage, sport)
    event_ids = _event_ids_by_date_and_type(storage, sport)

    events_out = {}
    for espn_event in espn_events:
        for competition in espn_event.get("competitions", []):
            session_type = (competition.get("type") or {}).get("abbreviation")
            our_event_type = _RELEVANT_SESSION_TYPES.get(session_type)
            if our_event_type is None:
                continue  # FP1/FP2/FP3/Qual -- no stored event of our own to join against

            competition_date = (competition.get("date") or "")[:10]
            our_event_id = event_ids.get((competition_date, our_event_type))
            if our_event_id is None:
                continue  # not yet backfilled/normalized into our own storage

            status = (competition.get("status") or {}).get("type", {})
            state = status.get("state")
            is_live = state == "in"
            was_recently_live = (
                (last_active := _parse_datetime(last_active_at.get(our_event_id))) is not None
                and now <= last_active + END_BUFFER
            )
            if not is_live and not was_recently_live:
                continue

            entry = {
                "event_type": our_event_type,
                "status": status.get("name"),
                "state": state,
                "race_name": espn_event.get("name"),
                "participants": _competition_participants(competition, roster_by_name),
            }
            if is_live:
                entry["last_active_at"] = now.isoformat()
            elif our_event_id in last_active_at:
                entry["last_active_at"] = last_active_at[our_event_id]  # preserve through the end-buffer tail
            events_out[our_event_id] = entry

    _put_cache(s3, bucket, {"fetched_at": now.isoformat(), "events": events_out})
    logger.info("Refreshed F1 live state for %d event(s)", len(events_out))
    return {"polled": len(events_out)}


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
