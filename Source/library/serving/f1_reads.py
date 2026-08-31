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

from library.serving.common import enrich_participants
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


def _entry(storage, sport: str, event: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "event_type": event.get("event_type"),
        "event_date": event.get("event_date"),
        "status": event.get("status"),
        "season": event.get("season"),
        "week": event.get("week"),
        "race_name": event.get("race_name"),
        "circuit_id": event.get("circuit_id"),
        "participants": enrich_participants(storage, sport, event.get("participants"), entity_type="player"),
        "venue_name": event.get("venue_name"),
        "venue_city": event.get("venue_city"),
        "venue_state": event.get("venue_state"),
    }


def list_events(storage, sport: str, status: str) -> dict:
    """GET /f1/events?status=scheduled|completed -- every stored event at
    that status, across both event_types ("field"/"sprint") unfiltered;
    the frontend uses event_type to decide how to render each one before
    it ever calls GET /f1/predictions/events/{event_id}. Same "no
    date-bucketing, one event = one entry" shape pga_reads.py's own
    list_events uses (F1's grouping unit is one race weekend, not one
    calendar date)."""
    events = storage.get_all_events(sport, status=status)

    if not events:
        return {"sport": sport, "events": []}

    with ThreadPoolExecutor(max_workers=min(len(events), 16)) as executor:
        entries = list(executor.map(lambda e: _entry(storage, sport, e), events))

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
