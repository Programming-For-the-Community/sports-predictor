"""
Read-only F1 serving logic -- GET /f1/events, GET /f1/season -- shared
between the heavy inference Lambda (Source/aws-lambdas/f1/predict) and the
light read-only Lambda (Source/aws-lambdas/f1/predict-read). GET /f1/models
reuses library.serving.common.list_models directly (fully generic, no
F1-specific wrapper needed), same as PGA's own pga_reads.py.

Holds the F1 model-name constants for the same reason pga_reads.py's own
docstring gives: predict-read's current_model_versions freshness check
needs them WITHOUT importing event_prediction.py (which imports
model_loader.py, which imports library.ml.model_types -- xgboost/sklearn/
lightgbm, the exact ML dependency weight predict-read is built to avoid).
event_prediction.py imports these constants FROM here instead.

Two event_types share this sport ("field" for the main race weekend,
"sprint" for a Sprint race -- library/normalize/f1.py's own module
docstring), each with its own distinct model set -- model_versions_for
dispatches the same way pga_reads.py's own version does for PGA's 3
event_types.
"""
from concurrent.futures import ThreadPoolExecutor

from library.serving.common import RECENT_EVENTS_LIMIT, enrich_participants, prefetch_entities
from library.storage.season_projections import season_projection_key

FIELD_EVENT_MODELS = {
    "win_probability": "win-probability",
    "podium_probability": "podium-probability",
    "projected_finish_position": "projected-finish-position",
    "dnf_probability": "dnf-probability",
    "projected_qualifying_position": "projected-qualifying-position",
}
CONSTRUCTOR_MODEL_NAME = "constructor-win-probability"
SPRINT_EVENT_MODELS = {
    "win_probability": "sprint-win-probability",
    "podium_probability": "sprint-podium-probability",
    "projected_grid_position": "projected-sprint-grid-position",
}

# Every model name a field/sprint response can ever score against, keyed
# for prediction_cache.current_model_versions -- same "always the FULL
# set, conservative" reasoning pga_reads.py's own FIELD_EVENT_MODEL_
# VERSIONS documents (a race with only 1 constructor's data resolved would
# otherwise never compare against the constructor model's own version).
FIELD_EVENT_MODEL_VERSIONS = {**FIELD_EVENT_MODELS, "constructor_win_probability": CONSTRUCTOR_MODEL_NAME}
SPRINT_EVENT_MODEL_VERSIONS = dict(SPRINT_EVENT_MODELS)


def model_versions_for(event_type: str) -> dict[str, str]:
    return {"field": FIELD_EVENT_MODEL_VERSIONS, "sprint": SPRINT_EVENT_MODEL_VERSIONS}[event_type]


def result_fingerprint(event: dict) -> int:
    """A cheap, monotonically-increasing signal that a race's real
    per-driver state has changed -- strictly increases as qualifying lands
    (result["qualifying"]["position"] populated, Saturday) and again once
    the race itself completes (result["status"] populated, Sunday) --
    mirrors pga_reads.py's own rounds_fingerprint, generalized for F1's
    two-stage (qualifying then race) reveal instead of PGA's 4-round one.

    Unlike PGA's own grid_position/finish_position, which both land in
    the SAME race-results payload post-race (library/normalize/f1.py's
    own _driver_result), qualifying_position genuinely arrives a full day
    earlier -- this is exactly the transition that matters for a live
    prediction to recompute against (see aws-lambdas/f1/predict/
    live_features.py's own grid_position-from-qualifying substitution),
    so it's counted as its own increment, not folded into `status`
    landing.

    Works unchanged for a "sprint" event too -- sprint has no qualifying
    component of its own (see library/features/f1.py's build_sprint_
    event_features docstring), so only the `status` half of this sum ever
    moves there; harmless, no separate function needed."""
    total = 0
    for participant in event.get("participants", []):
        result = participant.get("result") or {}
        if result.get("status") is not None:
            total += 1
        if (result.get("qualifying") or {}).get("position") is not None:
            total += 1
    return total


def _entry(storage, sport: str, event: dict, entity_cache: dict[tuple[str, str], dict]) -> dict:
    return {
        "event_id": event["event_id"],
        "event_type": event.get("event_type"),
        "event_date": event.get("event_date"),
        "status": event.get("status"),
        "season": event.get("season"),
        "week": event.get("week"),
        "race_name": event.get("race_name"),
        "circuit_id": event.get("circuit_id"),
        "participants": enrich_participants(
            storage, sport, event.get("participants"), entity_type="player", entity_cache=entity_cache,
        ),
        "venue_name": event.get("venue_name"),
        "venue_city": event.get("venue_city"),
        "venue_state": event.get("venue_state"),
    }


def _current_race_weekend_events(completed: list[dict]) -> list[dict]:
    """Every event sharing the most recently-dated event's own (season,
    round) -- NOT most_recent_event's single-event narrowing, which
    silently dropped one race on every real Sprint weekend. A Sprint
    weekend writes the Saturday sprint race and the Sunday Grand Prix as
    TWO separate events (event_type "sprint"/"field", event_id suffixed
    "-sprint" on the former -- library/normalize/f1.py's own
    sprint_result_to_event_item), sharing the same `week` (the season's
    round number, not a calendar week) but different event_dates --
    most_recent_event's own max()-by-date kept only the later-dated one
    (always the Sunday Grand Prix, since the sprint runs the day before
    it), the same architectural gap nfl_reads.py's own _previous_week_
    events/_week_key already solves for multi-game weeks, just keyed by
    round instead of a (season_type, week) triple since F1 has no
    season_type."""
    if not completed:
        return []
    latest = max(completed, key=lambda e: e.get("event_date", ""))
    target = (latest.get("season"), latest.get("week"))
    return [e for e in completed if (e.get("season"), e.get("week")) == target]


def list_events(storage, sport: str, status: str) -> dict:
    """GET /f1/events?status=scheduled|completed -- across both
    event_types ("field"/"sprint") unfiltered; the frontend uses
    event_type to decide how to render each one before it ever calls GET
    /f1/predictions/events/{event_id}. status=completed is bounded to the
    single most recent race weekend (_current_race_weekend_events) -- up
    to 2 events on a Sprint weekend, 1 otherwise -- same "just the most
    recent bucket, not full history" shape pga_reads.py's own list_events
    uses, for the same reason: unbounded history plus per-participant
    entity GetItems was a real production 504 there, and F1's own
    20-driver field times the same "every race ever backfilled" shape is
    the same architectural gap, just smaller (real complaint 2026-09-01:
    "I have noticed this with other sports").

    Bounded to RECENT_EVENTS_LIMIT rows on the query itself, most-recent-
    first -- an unbounded get_all_events call here paginates through the
    sport's entire completed-event history before ever discarding
    everything but one race weekend, a real production 504 confirmed live
    2026-09-02 (see pga_reads.py's own list_events docstring)."""
    if status == "completed":
        events = storage.get_all_events(sport, status=status, limit=RECENT_EVENTS_LIMIT)
        events = _current_race_weekend_events(events)
    else:
        events = storage.get_all_events(sport, status=status)

    if not events:
        return {"sport": sport, "events": []}

    refs = [(p["entity_id"], "player") for event in events for p in (event.get("participants") or [])]
    entity_cache = prefetch_entities(storage, sport, refs)

    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(lambda e: _entry(storage, sport, e, entity_cache), events))

    return {"sport": sport, "events": entries}


def get_season_projection(s3, sport: str) -> dict | None:
    """GET /f1/season -- reads the championship standings/probability
    projection (driver AND constructor standings, from the same simulated
    pass -- see aws-lambdas/f1/predict/season_simulation.py's own
    simulate_season) written weekly by the scheduled compute path
    (aws-lambdas/f1/predict/season_projection.py's run_scheduled), never
    computed live here. None if the schedule hasn't fired yet -- the
    caller surfaces that as a 503, same as every other sport."""
    key = season_projection_key(sport)
    if not s3.object_exists(key):
        return None
    return s3.get_json(key)
