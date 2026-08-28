"""
Read-only PGA serving logic -- GET /pga/events -- shared between the heavy
inference Lambda (Source/aws-lambdas/pga/predict) and the light read-only
Lambda (Source/aws-lambdas/pga/predict-read). GET /pga/models reuses
library.serving.common.list_models directly (fully generic, no PGA-
specific wrapper needed).

No date-bucketing, unlike nba_reads.py's own list_events -- PGA's
grouping unit is one tournament (one event_key), not one calendar date,
so there's nothing to bucket: every event already matching `status` is
returned as-is.

No prediction_comparison/leaders_comparison block yet (nba_reads.py's own
predicted-vs-actual audit-trail comparison for a completed event) -- a
field-wide version of that (predicted-vs-actual across ~150 golfers) is a
materially bigger, separately-scoped problem than this pass's serving
Lambda covers; list_events here only returns each event's own stored
shape plus entity enrichment.

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

from library.serving.common import enrich_participants
from library.storage.season_projections import season_projection_key

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


def _enrich_match_play_participants(storage, sport: str, participants: list[dict] | None) -> list[dict] | None:
    """enrich_participants takes one entity_type for the whole list --
    correct for a "field" event (always "player") or a "cup" event
    (always "team"), but a match_play event's own two sides can each
    independently be team-typed or player-typed (see
    _match_play_entity_type). Resolves and enriches per participant,
    preserving order, rather than trying to force a mixed list through
    the single-entity_type helper."""
    if not participants:
        return participants
    return [
        enrich_participants(storage, sport, [participant], entity_type=_match_play_entity_type(participant))[0]
        for participant in participants
    ]


def _enrich_pga_participants(storage, sport: str, event: dict) -> list[dict] | None:
    event_type = event.get("event_type")
    participants = event.get("participants")
    if event_type == "cup":
        return enrich_participants(storage, sport, participants, entity_type="team")
    if event_type == "match_play":
        return _enrich_match_play_participants(storage, sport, participants)
    # "field" (and any future/unrecognized event_type -- golfer entities
    # are the only kind a stroke-play field ever carries).
    return enrich_participants(storage, sport, participants, entity_type="player")


def _entry(storage, sport: str, event: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "event_type": event.get("event_type"),
        "event_date": event.get("event_date"),
        "end_date": event.get("end_date"),
        "status": event.get("status"),
        "season": event.get("season"),
        "tournament_name": event.get("tournament_name"),
        "participants": _enrich_pga_participants(storage, sport, event),
        "venue_name": event.get("venue_name"),
        "venue_city": event.get("venue_city"),
        "venue_state": event.get("venue_state"),
    }


def list_events(storage, sport: str, status: str) -> dict:
    """GET /pga/events?status=scheduled|completed -- every stored event at
    that status, across all 3 event_types ("field"/"match_play"/"cup")
    unfiltered; the frontend uses event_type to decide how to render each
    one before it ever calls GET /pga/predictions/events/{event_id}."""
    events = storage.get_all_events(sport, status=status)

    if not events:
        return {"sport": sport, "events": []}

    # Concurrent, not sequential -- each entry makes its own get_entity
    # round trips (one per participant, or up to ~150 for a full field),
    # independent per event.
    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(lambda e: _entry(storage, sport, e), events))

    return {"sport": sport, "events": entries}


def get_season_projection(s3, sport: str) -> dict | None:
    """GET /pga/season -- reads the FedEx Cup standings/Playoffs-
    probability projection written weekly by the scheduled compute path
    (aws-lambdas/pga/predict/season_projection.py's run_scheduled), never
    computed live here. None if the schedule hasn't fired yet -- the
    caller surfaces that as a 503, same as every other sport."""
    key = season_projection_key(sport)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)
