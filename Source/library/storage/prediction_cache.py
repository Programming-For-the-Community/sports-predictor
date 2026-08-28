"""
S3-backed read-through cache for on-demand predictions (GET .../predictions/...),
with async populate-on-miss. Sport-parameterized, shared between NFL and
NCAAFB. No ML dependencies -- importable from the light predict-read
Lambda.

Freshness: a cache entry is valid only while its model_versions still
match what's currently promoted. A completed event's entry never
otherwise expires; a scheduled event's also expires after
STALE_AFTER_SECONDS.
"""
import time

from library.storage.model_artifacts import current_version_key

STALE_AFTER_SECONDS = 12 * 60 * 60  # matches daily ingest cadence
IN_PROGRESS_TTL_SECONDS = 330  # must exceed the predict Lambda's own timeout

ERROR_STATUS_CODES = {
    "EventNotFoundError": 404,
    "MalformedEventError": 422,
    "NoPromotedModelError": 503,
}
ERROR_TTL_SECONDS = 5 * 60

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


def current_model_versions(s3, sport: str, models: dict[str, str]) -> dict[str, int | None]:
    """{key: version} for each `models` entry (key -> model_name), None if
    unpromoted. `models` lets a caller supply its own model-name map
    instead of this module hardcoding one shape -- CORE_EVENT_MODELS
    below is specifically the head-to-head sports' (NFL/NCAAFB/NBA/
    NCAAMBB) own map, not the only one that exists; a field-event sport
    (PGA) has a genuinely different model set (top10/top5/score/cutline/
    round, none of them win-probability/margin/home-score/away-score)
    and builds its own map at its own call site rather than this module
    growing a second hardcoded constant per sport shape."""
    versions: dict[str, int | None] = {}
    for key, model_name in models.items():
        pointer_key = current_version_key(sport, model_name)
        versions[key] = s3.get_json(pointer_key)["version"] if s3.object_exists(pointer_key) else None
    return versions


def current_core_model_versions(s3, sport: str) -> dict[str, int | None]:
    """{"win_probability": version, ...} for each CORE_EVENT_MODELS entry, None if unpromoted."""
    return current_model_versions(s3, sport, CORE_EVENT_MODELS)


def current_player_prop_model_version(s3, sport: str, target_stat: str) -> int | None:
    pointer_key = current_version_key(sport, player_prop_model_name(target_stat))
    return s3.get_json(pointer_key)["version"] if s3.object_exists(pointer_key) else None


def get_cached(s3, cache_key: str) -> dict | None:
    if not s3.object_exists(cache_key):
        return None
    return s3.get_json(cache_key)


def is_fresh(entry: dict, current_model_versions, extra_fingerprint=None) -> bool:
    """extra_fingerprint is an optional second freshness dimension beyond
    model_versions, compared the same way (any mismatch -> stale).
    Defaults to None/unused, so every caller that doesn't pass it keeps
    today's exact behavior. Added for PGA (see library.serving.pga_reads.
    rounds_fingerprint): STALE_AFTER_SECONDS' 12h TTL matches every other
    sport's own once-daily ingest cadence, but was silently letting a PGA
    prediction computed before a round finished get served as "fresh" for
    up to 12h after that round's real results landed -- a fingerprint
    that changes exactly when new round data lands catches this without
    touching the TTL/model-version behavior any other sport relies on."""
    if entry.get("model_versions") != current_model_versions:
        return False
    if extra_fingerprint is not None and entry.get("extra_fingerprint") != extra_fingerprint:
        return False
    if entry.get("event_status") == "completed":
        return True
    return (time.time() - entry.get("cached_at_epoch", 0)) < STALE_AFTER_SECONDS


def put_cached(s3, cache_key: str, result: dict, model_versions, event_status: str | None, extra_fingerprint=None) -> None:
    s3.put_json(cache_key, {
        "model_versions": model_versions,
        "event_status": event_status,
        "extra_fingerprint": extra_fingerprint,
        "cached_at_epoch": time.time(),
        "result": result,
    })


def put_error_cached(s3, cache_key: str, error_type: str, message: str) -> None:
    """Short-lived negative cache entry for a recognized ERROR_STATUS_CODES failure."""
    s3.put_json(cache_key, {"error_type": error_type, "error": message, "cached_at_epoch": time.time()})


def is_error_entry(entry: dict) -> bool:
    return entry.get("error_type") is not None


def is_error_entry_fresh(entry: dict) -> bool:
    return (time.time() - entry.get("cached_at_epoch", 0)) < ERROR_TTL_SECONDS


def claim_in_progress(s3, cache_key: str) -> bool:
    """True if the caller should trigger a compute; False if another claim is still active. Not atomic."""
    marker_key = _in_progress_key(cache_key)
    if s3.object_exists(marker_key):
        marker = s3.get_json(marker_key)
        if time.time() - marker.get("started_at_epoch", 0) < IN_PROGRESS_TTL_SECONDS:
            return False
    s3.put_json(marker_key, {"started_at_epoch": time.time()})
    return True


def clear_in_progress(s3, cache_key: str) -> None:
    s3.delete_object(_in_progress_key(cache_key))
