"""
S3-backed cache for on-demand live predictions (GET .../predictions/...)
-- read-through with async populate-on-miss, NOT the scheduled whole-
league precompute pattern library.storage.season_projections' key
belongs to. A season projection is ONE expensive computation covering
every team; a specific event/player prediction is one of thousands of
possible on-demand combinations (every scheduled/completed event x every
tracked player x every stat), and precomputing all of them the way the
season tab does isn't practical -- see predict-read/handler.py's own
docstring for the full read-through-with-async-populate design.

Freshness is gated by the model version(s) actually used to produce a
cached entry, not blindly time-based: a model repromotion (weekly for
NFL, monthly for NCAAFB) always invalidates a cache entry regardless of
its age. On top of that, a COMPLETED event's own inputs never change
once the game is final, so a version match alone is enough to trust it
forever; a SCHEDULED event's rolling averages/Elo/presumptive-leader
search can still drift with new daily ingest even between retrains, so
that side additionally expires after STALE_AFTER_SECONDS (is_fresh).

Sport-parameterized and shared between NFL and NCAAFB (both predict/
predict-read Lambda pairs import this the same way they already import
model_artifacts.py/season_projections.py) -- one cache design for every
sport, not a per-sport guess about which one needs it (both do -- see
project history for why NCAAFB's much larger backfilled history is what
actually surfaced this, even though the fix isn't NCAAFB-specific).

No ML dependencies (pandas/xgboost/scikit-learn) -- deliberately, so the
light predict-read Lambda can import this to check cache freshness
without dragging in the heavy predict Lambda's own dependency footprint.
"""
import time

from library.storage.model_artifacts import current_version_key

# 12 hours -- see this module's own docstring. Matched to ingest's own
# cadence (both sports' daily ingest, sfn-ingest-orchestrator.tf, runs
# once a day), not chosen arbitrarily: a scheduled event's rolling
# averages/Elo/presumptive-leader inputs can't actually change more than
# once a day, so refreshing more often than that just re-runs the same
# expensive computation for no new data.
STALE_AFTER_SECONDS = 12 * 60 * 60
IN_PROGRESS_TTL_SECONDS = 150  # comfortably longer than the 120s predict Lambda timeout

# A compute that fails with one of these recognized, EXPECTED-to-be-
# transient errors (event_id not ingested yet, no model promoted yet)
# gets a short-lived negative cache entry instead of leaving the request
# stuck at "computing" forever -- see put_error_cached's own docstring.
# Anything else (a real bug) is deliberately NOT caught here -- it
# propagates uncaught out of compute_and_cache_event/_player_prop, shows
# up as a real Lambda error, and simply gets retried on the next request
# once claim_in_progress's own TTL passes.
ERROR_STATUS_CODES = {
    "EventNotFoundError": 404,
    "MalformedEventError": 422,
    "NoPromotedModelError": 503,
}
ERROR_TTL_SECONDS = 5 * 60

# Every sport's event-prediction route scores these same four models,
# under these same model_name strings (see nfl_reads.py's/ncaafb_reads.py's
# own WIN_PROBABILITY_MODEL/SCORE_MODELS constants, which this
# intentionally matches) -- a leader-block player-prop model repromotion
# does NOT gate this cache's freshness (leaders are already best-effort,
# and in practice every model for a sport retrains in the same monthly/
# weekly run, so these four moving is a good proxy for "recomputed since
# the last retrain").
CORE_EVENT_MODELS = {
    "win_probability": "win-probability",
    "margin": "score-margin",
    "home_score": "home-score",
    "away_score": "away-score",
}


def player_prop_model_name(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def event_prediction_cache_key(sport: str, event_key: str) -> str:
    return f"predictions-cache/{sport}/events/{event_key}.json"


def player_prop_cache_key(sport: str, event_key: str, entity_id: str, stat: str) -> str:
    return f"predictions-cache/{sport}/events/{event_key}/players/{entity_id}/{stat}.json"


def _in_progress_key(cache_key: str) -> str:
    return f"{cache_key}.in-progress"


def current_core_model_versions(s3, sport: str) -> dict[str, int | None]:
    """{"win_probability": version, "margin": version, ...} for whichever
    of CORE_EVENT_MODELS currently have a promoted version (None for one
    that doesn't). Four small current.json pointer reads, not a model
    artifact load -- cheap enough to do on every predict-read request.
    Used both to stamp a freshly-computed event prediction's own cache
    entry (predict Lambda) and to check an existing entry's freshness
    against what's CURRENTLY promoted (predict-read, via is_fresh)."""
    versions: dict[str, int | None] = {}
    for key, model_name in CORE_EVENT_MODELS.items():
        pointer_key = current_version_key(sport, model_name)
        versions[key] = s3.get_json(pointer_key)["version"] if s3.object_exists(pointer_key) else None
    return versions


def current_player_prop_model_version(s3, sport: str, target_stat: str) -> int | None:
    """The single promoted version for one player-prop stat's model, or
    None if it's never been promoted -- the player-prop cache's own
    equivalent of current_core_model_versions."""
    pointer_key = current_version_key(sport, player_prop_model_name(target_stat))
    return s3.get_json(pointer_key)["version"] if s3.object_exists(pointer_key) else None


def get_cached(s3, cache_key: str) -> dict | None:
    """The raw cache envelope ({"model_versions", "event_status",
    "cached_at_epoch", "result"}), or None if nothing's cached yet --
    NOT freshness-checked, see is_fresh."""
    if not s3.object_exists(cache_key):
        return None
    return s3.get_json(cache_key)


def is_fresh(entry: dict, current_model_versions) -> bool:
    """See this module's own docstring for the completed-vs-scheduled
    staleness rule. current_model_versions must be the SAME shape
    put_cached originally stored (current_core_model_versions' dict for
    an event entry, current_player_prop_model_version's bare int for a
    player-prop entry) -- an exact equality check either way."""
    if entry.get("model_versions") != current_model_versions:
        return False
    if entry.get("event_status") == "completed":
        return True
    return (time.time() - entry.get("cached_at_epoch", 0)) < STALE_AFTER_SECONDS


def put_cached(s3, cache_key: str, result: dict, model_versions, event_status: str | None) -> None:
    s3.put_json(cache_key, {
        "model_versions": model_versions,
        "event_status": event_status,
        "cached_at_epoch": time.time(),
        "result": result,
    })


def put_error_cached(s3, cache_key: str, error_type: str, message: str) -> None:
    """A short-lived NEGATIVE cache entry for a compute that failed with
    one of ERROR_STATUS_CODES' recognized errors -- e.g. event_id simply
    hasn't been ingested yet (EventNotFoundError), which might become
    true after the next ingest run, so this can't be cached as
    permanently wrong the way put_cached's own completed-event entries
    are cached as permanently right. Expires after ERROR_TTL_SECONDS
    (is_error_entry_fresh) rather than being version-gated like a real
    result -- there's no model involved in "this event doesn't exist"
    for a repromotion to invalidate.

    Distinct envelope shape from put_cached's own (no model_versions/
    event_status/result) -- is_error_entry is how a caller tells the two
    apart after a plain get_cached."""
    s3.put_json(cache_key, {"error_type": error_type, "error": message, "cached_at_epoch": time.time()})


def is_error_entry(entry: dict) -> bool:
    return entry.get("error_type") is not None


def is_error_entry_fresh(entry: dict) -> bool:
    return (time.time() - entry.get("cached_at_epoch", 0)) < ERROR_TTL_SECONDS


def claim_in_progress(s3, cache_key: str) -> bool:
    """Best-effort de-dupe for concurrent cache misses on the same key.
    True if the caller should go ahead and trigger a compute (either no
    one else has claimed it, or their claim is old enough to have surely
    already failed/timed out); False if someone else appears to already
    be computing it right now. NOT atomic -- S3Manager has no
    conditional-put -- so a narrow race can still let two requests both
    fire a compute for the same key; accepted as a harmless, bounded-cost
    duplicate (worst case, one extra async Lambda invocation) rather than
    a correctness problem worth a stronger guarantee."""
    marker_key = _in_progress_key(cache_key)
    if s3.object_exists(marker_key):
        marker = s3.get_json(marker_key)
        if time.time() - marker.get("started_at_epoch", 0) < IN_PROGRESS_TTL_SECONDS:
            return False
    s3.put_json(marker_key, {"started_at_epoch": time.time()})
    return True


def clear_in_progress(s3, cache_key: str) -> None:
    s3.delete_object(_in_progress_key(cache_key))
