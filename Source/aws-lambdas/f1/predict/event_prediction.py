"""
Prediction logic for one F1 event (field/sprint), and the
compute_and_cache_event background worker that populates
library.storage.prediction_cache on a cache miss.
"""
import logging
from datetime import datetime, timezone

import live_features
import model_loader
from library.schema.keys import event_key as build_event_key
from library.serving.f1_reads import (
    CONSTRUCTOR_MODEL_NAME,
    FIELD_EVENT_MODELS,
    SPRINT_EVENT_MODELS,
    model_versions_for,
    result_fingerprint,
)
from library.storage import prediction_cache

logger = logging.getLogger("f1-predict")

SPORT = "f1"


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Loads each distinct model at most once per request."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, SPORT, model_name)
    return model_cache[model_name]


def record_prediction(predictions_table, event_key_value: str, model_key: str, value) -> None:
    predictions_table.put_item({
        "event_key": event_key_value,
        "model_key": model_key,
        "predicted_value": value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def _score(model_cache: dict, s3, predictions_table, event_key_value: str, model_name: str, feature_row: dict, record_suffix: str) -> dict | None:
    """{"value": ..., "model_version": ...}, or None if model_name has no
    promoted version. Callers tolerate a missing model per-key."""
    try:
        estimator, model_card = get_cached_model(model_cache, s3, model_name)
    except model_loader.NoPromotedModelError:
        return None
    value = model_loader.predict(estimator, model_card, feature_row)
    record_prediction(
        predictions_table, event_key_value, f"MODEL#{model_name}#v{model_card['version']}#{record_suffix}", {"value": value},
    )
    return {"value": value, "model_version": model_card["version"]}


def _entity_name(storage, entity_id: str, entity_type: str) -> dict:
    entity = storage.get_entity(SPORT, entity_id, entity_type)
    if entity is None:
        return {"name": None}
    return {"name": entity.get("name")}


def _driver_entry_base(storage, entity_id: str, constructor_entity_id: str | None, predictions: dict) -> dict:
    """Shared entry shape for both predict_field_event/predict_sprint_
    event's own per-driver rows -- the driver's own name via _entity_name,
    PLUS the constructor's own real display name (not just its raw
    entity_id, which is a lowercase/underscored id like "red_bull" -- see
    library/normalize/f1.py's _constructor_entities: "name" is Jolpica's
    own real constructor name, e.g. "Red Bull". A real gap found live
    2026-08-31: the frontend's own driver-row subtitle was showing this
    raw id verbatim with no name lookup at all). constructor_name is None
    when constructor_entity_id itself is None or the entity lookup comes
    back empty -- the frontend falls back to humanizing the id itself in
    that case, same defensive discipline every other name field here
    already has."""
    constructor_name = None
    if constructor_entity_id is not None:
        constructor_entity = storage.get_entity(SPORT, constructor_entity_id, "team")
        constructor_name = (constructor_entity or {}).get("name")
    return {
        "entity_id": entity_id, **_entity_name(storage, entity_id, "player"),
        "constructor_entity_id": constructor_entity_id, "constructor_name": constructor_name,
        "predictions": predictions,
    }


def _actual_driver_result(participant: dict) -> dict | None:
    """Real, already-stored result -- meaningful once qualifying has
    landed even before the race itself runs, not just once "completed"."""
    result = participant.get("result") or {}
    qualifying = result.get("qualifying")
    if result.get("status") is None and qualifying is None:
        return None
    return {
        "finish_position": result.get("finish_position"),
        "grid_position": result.get("grid_position"),
        "status": result.get("status"),
        "points": result.get("points"),
        "fastest_lap": result.get("fastest_lap"),
        "laps_completed": result.get("laps_completed"),
        "qualifying": qualifying,
    }


def _field_sort_key(entry: dict):
    """Ascending by projected_finish_position (field events); falls back
    to projected_grid_position (sprint events, which have no finish-
    position model at all -- see model-training/f1/train_sprint_*.py),
    then to win_probability descending if neither is promoted.

    The frontend's own leaderboard relies on this exact order to display
    a driver's ROW RANK as their projected position instead of the raw
    regression value rounded independently per row -- two close-but-
    distinct floats can round to the SAME integer, which reads as an
    impossible shared finishing/grid slot (real complaint 2026-08-31).
    Row rank is always unique by construction; see f1_leaderboard_table.
    dart's own _PositionCell."""
    finish = entry["predictions"].get("projected_finish_position")
    if finish is not None:
        return (0, finish["value"])
    grid = entry["predictions"].get("projected_grid_position")
    if grid is not None:
        return (1, grid["value"])
    win = entry["predictions"].get("win_probability")
    if win is not None:
        return (2, -win["value"])
    return (3, 0)


def _assign_qualifying_ranks(field: list[dict]) -> None:
    """Mutates each entry's own predictions["projected_qualifying_position"]
    in place, adding a "rank" alongside "value" -- the same "row rank
    instead of independently-rounded raw value" fix _field_sort_key/
    f1_leaderboard_table.dart's _PositionCell already apply to FINISH/GRID,
    now extended to QUALIFYING (real complaint 2026-09-01: two close-but-
    distinct projected_qualifying_position values rounding to the same
    integer showed as duplicate qualifying positions). Qualifying isn't
    the field's own sort key (see _field_sort_key -- FINISH takes
    priority), so this ranks independently of field's own row order rather
    than reusing it. Ties broken by entity_id for a fully deterministic
    order across repeat computations, not just an incidental stable-sort
    artifact."""
    ranked = sorted(
        (entry for entry in field if entry["predictions"].get("projected_qualifying_position") is not None),
        key=lambda entry: (entry["predictions"]["projected_qualifying_position"]["value"], entry["entity_id"]),
    )
    for rank, entry in enumerate(ranked, start=1):
        entry["predictions"]["projected_qualifying_position"]["rank"] = rank


def predict_field_event(storage, s3, predictions_table, event_id: str) -> dict:
    built = live_features.build_live_field_features(storage, SPORT, event_id)
    event = built["event"]
    event_key_value = build_event_key(SPORT, event_id)
    model_cache: dict = {}
    participants_by_id = {p["entity_id"]: p for p in event.get("participants", [])}

    field = []
    for entity_id, row in built["driver_rows"].items():
        predictions = {}
        for key, model_name in FIELD_EVENT_MODELS.items():
            scored = _score(model_cache, s3, predictions_table, event_key_value, model_name, row, f"DRIVER#{entity_id}")
            if scored is not None:
                predictions[key] = scored

        entry = _driver_entry_base(storage, entity_id, row.get("constructor_entity_id"), predictions)
        participant = participants_by_id.get(entity_id)
        if participant is not None:
            actual = _actual_driver_result(participant)
            if actual is not None:
                entry["actual"] = actual
        field.append(entry)

    _assign_qualifying_ranks(field)
    field.sort(key=_field_sort_key)

    constructors = []
    for constructor_id, row in built["constructor_rows"].items():
        scored = _score(model_cache, s3, predictions_table, event_key_value, CONSTRUCTOR_MODEL_NAME, row, f"CONSTRUCTOR#{constructor_id}")
        if scored is not None:
            constructors.append({
                "entity_id": constructor_id, **_entity_name(storage, constructor_id, "team"),
                "predictions": {"win_probability": scored},
            })
    constructors.sort(key=lambda c: -c["predictions"]["win_probability"]["value"])

    return {
        "sport": SPORT, "event_key": event_key_value, "event_id": event_id, "event_type": "field",
        "race_name": event.get("race_name"), "status": event.get("status"),
        "circuit_id": event.get("circuit_id"), "season": event.get("season"), "week": event.get("week"),
        "field": field, "constructors": constructors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def predict_sprint_event(storage, s3, predictions_table, event_id: str) -> dict:
    built = live_features.build_live_sprint_features(storage, SPORT, event_id)
    event = built["event"]
    event_key_value = build_event_key(SPORT, event_id)
    model_cache: dict = {}
    participants_by_id = {p["entity_id"]: p for p in event.get("participants", [])}

    field = []
    for entity_id, row in built["driver_rows"].items():
        predictions = {}
        for key, model_name in SPRINT_EVENT_MODELS.items():
            scored = _score(model_cache, s3, predictions_table, event_key_value, model_name, row, f"DRIVER#{entity_id}")
            if scored is not None:
                predictions[key] = scored

        entry = _driver_entry_base(storage, entity_id, row.get("constructor_entity_id"), predictions)
        participant = participants_by_id.get(entity_id)
        if participant is not None:
            actual = _actual_driver_result(participant)
            if actual is not None:
                entry["actual"] = actual
        field.append(entry)

    field.sort(key=_field_sort_key)

    return {
        "sport": SPORT, "event_key": event_key_value, "event_id": event_id, "event_type": "sprint",
        "race_name": event.get("race_name"), "status": event.get("status"),
        "circuit_id": event.get("circuit_id"), "season": event.get("season"), "week": event.get("week"),
        "field": field,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def predict_event(storage, s3, predictions_table, event_id: str) -> dict:
    """Dispatches on the stored event's own event_type. Raises
    live_features.EventNotFoundError if no such event exists;
    live_features.MalformedEventError for an unrecognized event_type."""
    event_key_value = build_event_key(SPORT, event_id)
    event = storage.get_event(event_key_value)
    if event is None:
        raise live_features.EventNotFoundError(f"No stored F1 event for {event_id!r}")

    event_type = event.get("event_type")
    if event_type == "field":
        return predict_field_event(storage, s3, predictions_table, event_id)
    if event_type == "sprint":
        return predict_sprint_event(storage, s3, predictions_table, event_id)
    raise live_features.MalformedEventError(f"Event {event_id!r} has an unrecognized event_type {event_type!r}")


def compute_and_cache_event(storage, s3, predictions_table, event_id: str) -> None:
    """Background worker triggered by predict-read on a cache miss/stale
    refresh. A recognized error gets a short-lived negative cache entry;
    any other exception propagates after the in-progress claim clears."""
    event_key_value = build_event_key(SPORT, event_id)
    cache_key = prediction_cache.event_prediction_cache_key(SPORT, event_key_value)
    try:
        try:
            result = predict_event(storage, s3, predictions_table, event_id)
        except (live_features.EventNotFoundError, live_features.MalformedEventError, model_loader.NoPromotedModelError) as exc:
            prediction_cache.put_error_cached(s3, cache_key, type(exc).__name__, str(exc))
            return
        model_versions = prediction_cache.current_model_versions(s3, SPORT, model_versions_for(result["event_type"]))
        # Fresh fetch so the fingerprint reflects the event state this
        # prediction was actually computed against.
        event = storage.get_event(event_key_value)
        extra_fingerprint = result_fingerprint(event) if event is not None else None
        prediction_cache.put_cached(s3, cache_key, result, model_versions, result.get("status"), extra_fingerprint)
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)
