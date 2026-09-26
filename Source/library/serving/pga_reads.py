"""
Read-only PGA serving logic -- GET /pga/events -- shared between the heavy
inference Lambda (Source/aws-lambdas/pga/predict) and the light read-only
Lambda (Source/aws-lambdas/pga/predict-read). GET /pga/models reuses
library.serving.common.list_models directly (fully generic, no PGA-
specific wrapper needed).

No week/day-bucketing the way nba_reads.py's own list_events has -- PGA's
grouping unit is already one tournament (one event_key), not one calendar
date, so there's no smaller bucket to group multiple events into.
status=completed still bounds to the single most recent tournament
(library.serving.common.most_recent_event) rather than returning every
completed tournament ever backfilled -- unbounded history here doesn't
scale (up to ~150 golfers x every historical tournament, sequential
entity GetItems with no cache). status=scheduled stays unbounded -- there
are only ever a handful of future tournaments in the registry at once,
so it's never shown the same symptom.

No prediction_comparison/leaders_comparison block (nba_reads.py's own
predicted-vs-actual audit-trail comparison for a completed event) --
list_events here only returns each event's own stored shape plus entity
enrichment.

Also holds the PGA model-name constants (FIELD_EVENT_MODELS etc.) --
deliberately here, not in aws-lambdas/pga/predict/event_prediction.py,
so predict-read's own current_model_versions freshness check can read
them WITHOUT importing event_prediction.py (which imports model_loader.py,
which imports library.ml.model_types -- xgboost/sklearn/lightgbm, the
exact ML dependency weight predict-read is built to avoid; see this
Lambda's own docstring). event_prediction.py imports these constants
FROM here instead, same "lightweight *_reads.py module both predict and
predict-read import from" split nba_reads.py's own WIN_PROBABILITY_MODEL/
SCORE_MODELS constants already establish.

Callers own their own storage/s3 objects and Lambda-lifecycle concerns.
"""
from concurrent.futures import ThreadPoolExecutor

from library.schema.keys import event_key
from library.serving.common import enrich_participants, get_season_projection, most_recent_event, prefetch_entities

FIELD_EVENT_MODELS = {
    "top_10_probability": "top-10-probability",
    "top_5_probability": "top-5-probability",
    "projected_score_to_par": "projected-score-to-par",
}
CUTLINE_MODEL_NAME = "projected-cut-line"
ROUND_MODEL_NAMES = {1: "round-1", 2: "round-2", 3: "round-3", 4: "round-4"}
MATCH_MODEL_NAME = "match-win-probability"
CUP_MODEL_NAME = "cup-win-probability"

# Every model name a field response can ever score against, keyed for
# prediction_cache.current_model_versions -- always the FULL set
# regardless of which round models a given request actually invoked
# (applicability varies per golfer, see aws-lambdas/pga/predict/
# live_features.py's applicable_rounds). Comparing against the full map
# is conservative (may trigger an occasional unneeded refresh for a
# field with no golfer needing, say, round-4 this week) but never serves
# a genuinely stale result the way a per-request-partial map could.
FIELD_EVENT_MODEL_VERSIONS = {
    **FIELD_EVENT_MODELS,
    "cutline": CUTLINE_MODEL_NAME,
    **{f"round_{n}": name for n, name in ROUND_MODEL_NAMES.items()},
}
MATCH_MODEL_VERSIONS = {"match_win_probability": MATCH_MODEL_NAME}
CUP_MODEL_VERSIONS = {"cup_win_probability": CUP_MODEL_NAME}

# Most-recent completed rows read to find the latest tournament-level row --
# a finished Ryder Cup/Presidents Cup puts its ~28-30 match rows (dated
# later than the cup row itself) ahead of it.
COMPLETED_LOOKBACK_ROWS = 64


def model_versions_for(event_type: str) -> dict[str, str]:
    return {"field": FIELD_EVENT_MODEL_VERSIONS, "match_play": MATCH_MODEL_VERSIONS, "cup": CUP_MODEL_VERSIONS}[event_type]


def rounds_fingerprint(event: dict) -> int | None:
    """A cheap, monotonically-increasing signal that a field event's real
    per-golfer round results have changed -- strictly increases exactly
    when a new round's results land for any golfer. Passed as
    prediction_cache.is_fresh/put_cached's own extra_fingerprint, so a
    cached prediction gets recomputed once real round-1 (or later)
    results are in, not just on STALE_AFTER_SECONDS' 12h TTL (which
    matches every other sport's once-daily ingest, not PGA's own
    live-scores cadence) or a model-version bump. None for match_play/cup
    (no per-round concept) -- leaves their existing TTL/model-version-only
    freshness behavior untouched, since is_fresh skips this check
    entirely when passed None."""
    if event.get("event_type") != "field":
        return None
    return sum(len((p.get("result") or {}).get("rounds", [])) for p in event.get("participants", []))


def _match_play_entity_type(participant: dict) -> str:
    """A match_play participant's own entity_id is either a national
    TEAM id (foursomes/fourball/singles at Ryder Cup/Presidents Cup,
    never present in the participant's own golfer_entity_ids -- a
    disjoint id space) or an individual GOLFER's id doubling as its own
    entity_id (WGC-Dell Technologies Match Play, no team layer --
    library/normalize/pga_matchplay.py's _match_participant docstring:
    "the golfer's own id serves double duty as both entity_id and its
    own single-element golfer_entity_ids"). Checking membership, not
    event-level metadata, is what actually distinguishes the two cases
    -- a team vs. individual match_play event isn't tagged anywhere else
    on the stored item."""
    return "player" if participant["entity_id"] in participant.get("golfer_entity_ids", []) else "team"


def _entity_refs(event: dict) -> list[tuple[str, str]]:
    """Every (entity_id, entity_type) this one event's own enrichment will
    need -- the prefetch list list_events builds once across every event
    in the (now-bounded, see list_events) result, instead of each event
    resolving its own participants independently."""
    event_type = event.get("event_type")
    participants = event.get("participants") or []
    if event_type == "cup":
        return [(p["entity_id"], "team") for p in participants]
    if event_type == "match_play":
        return [
            *((p["entity_id"], _match_play_entity_type(p)) for p in participants),
            *((golfer_id, "player") for p in participants for golfer_id in _team_side_golfer_ids(p)),
        ]
    # "field" (and any future/unrecognized event_type).
    return [(p["entity_id"], "player") for p in participants]


def _team_side_golfer_ids(participant: dict) -> list[str]:
    """The golfers playing for a TEAM-typed match_play side -- empty for an
    individual (WGC) side, whose golfer is the participant itself."""
    if _match_play_entity_type(participant) != "team":
        return []
    return participant.get("golfer_entity_ids") or []


def _golfers(storage, sport: str, participant: dict, entity_cache: dict[tuple[str, str], dict] | None) -> list[dict]:
    golfers = []
    for golfer_id in _team_side_golfer_ids(participant):
        entity = (entity_cache or {}).get((golfer_id, "player")) or storage.get_entity(sport, golfer_id, "player")
        golfers.append({"entity_id": golfer_id, "name": (entity or {}).get("name")})
    return golfers


def _enrich_match_play_participants(
    storage, sport: str, participants: list[dict] | None, entity_cache: dict[tuple[str, str], dict] | None,
) -> list[dict] | None:
    """enrich_participants takes one entity_type for the whole list --
    correct for a "field" event (always "player") or a "cup" event
    (always "team"), but a match_play event's own two sides can each
    independently be team-typed or player-typed (see
    _match_play_entity_type). Resolves and enriches per participant,
    preserving order, rather than trying to force a mixed list through
    the single-entity_type helper."""
    if not participants:
        return participants
    enriched = []
    for participant in participants:
        entry = enrich_participants(
            storage, sport, [participant], entity_type=_match_play_entity_type(participant), entity_cache=entity_cache,
        )[0]
        golfers = _golfers(storage, sport, participant, entity_cache)
        enriched.append({**entry, "golfers": golfers} if golfers else entry)
    return enriched


def _enrich_pga_participants(
    storage, sport: str, event: dict, entity_cache: dict[tuple[str, str], dict] | None,
) -> list[dict] | None:
    event_type = event.get("event_type")
    participants = event.get("participants")
    if event_type == "cup":
        return enrich_participants(storage, sport, participants, entity_type="team", entity_cache=entity_cache)
    if event_type == "match_play":
        return _enrich_match_play_participants(storage, sport, participants, entity_cache)
    # "field" (and any future/unrecognized event_type -- golfer entities
    # are the only kind a stroke-play field ever carries).
    return enrich_participants(storage, sport, participants, entity_type="player", entity_cache=entity_cache)


def _entry(storage, sport: str, event: dict, entity_cache: dict[tuple[str, str], dict] | None) -> dict:
    return {
        "event_id": event["event_id"],
        "event_type": event.get("event_type"),
        "event_date": event.get("event_date"),
        "end_date": event.get("end_date"),
        "status": event.get("status"),
        "season": event.get("season"),
        "tournament_name": event.get("tournament_name"),
        "participants": _enrich_pga_participants(storage, sport, event, entity_cache),
        "venue_name": event.get("venue_name"),
        "venue_city": event.get("venue_city"),
        "venue_state": event.get("venue_state"),
        "session_name": event.get("session_name"),
        "match_time": event.get("match_time"),
    }


def _is_child_match(event: dict) -> bool:
    """A match_play row belonging to a parent tournament -- listed under
    that tournament (list_child_events), never as a tournament itself."""
    return event.get("parent_event_id") is not None


def _entries_response(storage, sport: str, events: list[dict]) -> dict:
    if not events:
        return {"sport": sport, "events": []}

    # One BatchGetItem pass across every event's own participants instead
    # of a GetItem per participant.
    refs = [ref for event in events for ref in _entity_refs(event)]
    entity_cache = prefetch_entities(storage, sport, refs)

    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(lambda e: _entry(storage, sport, e, entity_cache), events))

    return {"sport": sport, "events": entries}


def list_events(storage, sport: str, status: str) -> dict:
    """GET /pga/events?status=scheduled|completed -- one row per tournament
    ("field"/"cup", plus any match_play row without a parent); a cup's own
    matches come from list_child_events. The frontend uses event_type to
    decide how to render each one. status=completed is bounded to the
    single most recent tournament -- see this module's own docstring --
    reading at most COMPLETED_LOOKBACK_ROWS rows rather than the sport's
    entire completed history (1000+ PGA events)."""
    if status == "completed":
        events = storage.get_all_events(sport, status=status, limit=COMPLETED_LOOKBACK_ROWS)
        events = most_recent_event([e for e in events if not _is_child_match(e)])
    else:
        events = [e for e in storage.get_all_events(sport, status=status) if not _is_child_match(e)]
    return _entries_response(storage, sport, events)


def list_child_events(storage, sport: str, parent_event_id: str) -> dict:
    """GET /pga/events?parent_event_id={id} -- every match_play row of one
    tournament (Ryder Cup/Presidents Cup), any status, in tee-off order.
    Completed rows are bounded to on/after the parent's own start date."""
    parent = storage.get_event(event_key(sport, parent_event_id))
    if parent is None:
        return {"sport": sport, "events": []}
    candidates = [
        *storage.get_all_events(sport, status="scheduled"),
        *storage.get_all_events(sport, status="completed", since_date=parent.get("event_date") or None),
    ]
    events = sorted(
        (e for e in candidates if e.get("parent_event_id") == parent_event_id),
        key=lambda e: (e.get("match_time") or e.get("event_date") or "", e["event_id"]),
    )
    return _entries_response(storage, sport, events)
