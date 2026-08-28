"""
Prediction logic for one PGA event (event_type "field"/"match_play"/
"cup"), and the compute_and_cache_event background worker that populates
library.storage.prediction_cache on a cache miss.

One shared compute_and_cache_event for all 3 event_types -- cache-key
derivation, in-progress claiming, and error handling are identical
regardless of type; only the prediction body differs, isolated behind
predict_event's own event_type dispatch to predict_field_event/
predict_match_event/predict_cup_event.
"""
import logging
from datetime import datetime, timezone

import live_features
import model_loader
from library.schema.keys import event_key as build_event_key
from library.serving.pga_reads import (
    CUP_MODEL_NAME,
    CUTLINE_MODEL_NAME,
    FIELD_EVENT_MODELS,
    MATCH_MODEL_NAME,
    ROUND_MODEL_NAMES,
    model_versions_for,
)
from library.storage import prediction_cache

logger = logging.getLogger("pga-predict")

SPORT = "pga"


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
    """{"value": ..., "model_version": ...}, or None if model_name has
    never had a version promoted -- every caller tolerates a missing
    model per-key (record_prediction is simply skipped for that one),
    never all-or-nothing across a field/round/cutline/match response."""
    try:
        estimator, model_card = get_cached_model(model_cache, s3, model_name)
    except model_loader.NoPromotedModelError:
        return None
    value = model_loader.predict(estimator, model_card, feature_row)
    record_prediction(
        predictions_table, event_key_value, f"MODEL#{model_name}#v{model_card['version']}#{record_suffix}", {"value": value},
    )
    return {"value": value, "model_version": model_card["version"]}


def _golfer_name(storage, entity_id: str) -> dict:
    entity = storage.get_entity(SPORT, entity_id, "player")
    if entity is None:
        return {"name": None, "country": None}
    metadata = entity.get("metadata") or {}
    return {"name": entity.get("name"), "country": metadata.get("country")}


def _actual_golfer_result(participant: dict) -> dict | None:
    result = participant.get("result") or {}
    if result.get("finish_position") is None and result.get("score_to_par") is None:
        return None
    return {"finish_position": result.get("finish_position"), "score_to_par": result.get("score_to_par")}


def _field_sort_key(entry: dict):
    """Ascending by projected_score_to_par (lowest = best -- the "field
    finish order" build_golfer_event_features' own docstring calls out as
    a serving-time ranking of this model's predictions, not a separate
    artifact); falls back to top_10_probability descending if the score
    model has no promoted version yet."""
    score = entry["predictions"].get("projected_score_to_par")
    if score is not None:
        return (0, score["value"])
    top10 = entry["predictions"].get("top_10_probability")
    if top10 is not None:
        return (1, -top10["value"])
    return (2, 0)


def predict_field_event(storage, s3, predictions_table, event_id: str) -> dict:
    built = live_features.build_live_field_features(storage, SPORT, event_id)
    event = built["event"]
    event_key_value = build_event_key(SPORT, event_id)
    model_cache: dict = {}
    status = event.get("status")
    participants_by_id = {p["entity_id"]: p for p in event.get("participants", [])}

    field = []
    for entity_id, rows in built["golfer_rows"].items():
        predictions = {}
        for key, model_name in FIELD_EVENT_MODELS.items():
            scored = _score(model_cache, s3, predictions_table, event_key_value, model_name, rows["golfer"], f"GOLFER#{entity_id}")
            if scored is not None:
                predictions[key] = scored

        round_predictions = {}
        for round_number, round_row in rows["rounds"].items():
            scored = _score(
                model_cache, s3, predictions_table, event_key_value, ROUND_MODEL_NAMES[round_number], round_row, f"GOLFER#{entity_id}",
            )
            if scored is not None:
                round_predictions[f"round_{round_number}"] = scored
        if round_predictions:
            predictions["rounds"] = round_predictions

        entry = {"entity_id": entity_id, **_golfer_name(storage, entity_id), "predictions": predictions}
        if status == "completed":
            participant = participants_by_id.get(entity_id)
            if participant is not None:
                entry["actual"] = _actual_golfer_result(participant)
        field.append(entry)

    field.sort(key=_field_sort_key)

    cutline = None
    cutline_scored = _score(model_cache, s3, predictions_table, event_key_value, CUTLINE_MODEL_NAME, built["cutline_row"], "CUTLINE")
    if cutline_scored is not None:
        cutline = {"projected_cut_score": cutline_scored}

    return {
        "sport": SPORT, "event_key": event_key_value, "event_id": event_id, "event_type": "field",
        "tournament_name": event.get("tournament_name"), "status": status,
        "cutline": cutline,
        "field": field,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _match_side_entity_type(participant: dict) -> str:
    """Same rule as library.serving.pga_reads' own _match_play_entity_type
    (duplicated, not imported, to keep this predict-side module's own
    dependency surface -- boto3/xgboost/pandas already -- separate from
    library.serving's): a team match_play participant's entity_id is a
    national team id (never in its own golfer_entity_ids); an individual/
    WGC participant's entity_id doubles as its own single-element
    golfer_entity_ids[0]."""
    return "player" if participant["entity_id"] in participant.get("golfer_entity_ids", []) else "team"


def _match_side_summary(storage, participant: dict) -> dict:
    entity_type = _match_side_entity_type(participant)
    entity = storage.get_entity(SPORT, participant["entity_id"], entity_type)
    summary = {"entity_id": participant["entity_id"], "name": (entity or {}).get("name")}
    golfer_ids = participant.get("golfer_entity_ids") or []
    if entity_type == "team" and golfer_ids:
        summary["golfers"] = [{"entity_id": gid, **_golfer_name(storage, gid)} for gid in golfer_ids]
    return summary


def _cup_side_summary(storage, participant: dict) -> dict:
    entity = storage.get_entity(SPORT, participant["entity_id"], "team")
    return {"entity_id": participant["entity_id"], "name": (entity or {}).get("name")}


def _predict_two_sided_event(
    storage, event_key_value: str, event: dict, event_type: str,
    model_scored: dict | None, prediction_key: str, side_summary,
) -> dict:
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)

    result = {
        "sport": SPORT, "event_key": event_key_value, "event_id": event["event_id"], "event_type": event_type,
        "tournament_name": event.get("tournament_name"), "status": event.get("status"),
        "match_format": event.get("match_format"), "session_name": event.get("session_name"),
        "home": side_summary(storage, home) if home else None,
        "away": side_summary(storage, away) if away else None,
        "predictions": {prediction_key: model_scored} if model_scored is not None else {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if event.get("status") == "completed" and home is not None:
        home_result = home.get("result") or {}
        result["actual"] = {"home_won": home_result.get("won"), "halved": home_result.get("halved")}
    return result


def predict_match_event(storage, s3, predictions_table, event_id: str) -> dict:
    built = live_features.build_live_match_features(storage, SPORT, event_id)
    event = built["event"]
    event_key_value = build_event_key(SPORT, event_id)
    model_cache: dict = {}
    scored = _score(model_cache, s3, predictions_table, event_key_value, MATCH_MODEL_NAME, built["features"], "MATCH")
    return _predict_two_sided_event(
        storage, event_key_value, event, "match_play", scored, "match_win_probability", _match_side_summary,
    )


def predict_cup_event(storage, s3, predictions_table, event_id: str) -> dict:
    built = live_features.build_live_cup_features(storage, SPORT, event_id)
    event = built["event"]
    event_key_value = build_event_key(SPORT, event_id)
    model_cache: dict = {}
    scored = _score(model_cache, s3, predictions_table, event_key_value, CUP_MODEL_NAME, built["features"], "CUP")
    return _predict_two_sided_event(
        storage, event_key_value, event, "cup", scored, "cup_win_probability", _cup_side_summary,
    )


def predict_event(storage, s3, predictions_table, event_id: str) -> dict:
    """Dispatches on the stored event's own event_type. Raises
    live_features.EventNotFoundError if no such event exists;
    live_features.MalformedEventError for an unrecognized event_type
    (fail closed, matching every other PGA normalizer/dispatch's own
    convention) or one of the two live_features builders' own shape
    checks (missing home/away, no match_play session yet for a cup)."""
    event_key_value = build_event_key(SPORT, event_id)
    event = storage.get_event(event_key_value)
    if event is None:
        raise live_features.EventNotFoundError(f"No stored PGA event for {event_id!r}")

    event_type = event.get("event_type")
    if event_type == "field":
        return predict_field_event(storage, s3, predictions_table, event_id)
    if event_type == "match_play":
        return predict_match_event(storage, s3, predictions_table, event_id)
    if event_type == "cup":
        return predict_cup_event(storage, s3, predictions_table, event_id)
    raise live_features.MalformedEventError(f"Event {event_id!r} has an unrecognized event_type {event_type!r}")


def compute_and_cache_event(storage, s3, predictions_table, event_id: str) -> None:
    """Background worker triggered by predict-read on a cache miss/stale-
    refresh. A recognized, possibly-transient error (event not ingested,
    wrong shape, no model promoted, no match_play session yet for a cup)
    gets a short-lived negative cache entry; any other exception
    propagates after the in-progress claim clears."""
    event_key_value = build_event_key(SPORT, event_id)
    cache_key = prediction_cache.event_prediction_cache_key(SPORT, event_key_value)
    try:
        try:
            result = predict_event(storage, s3, predictions_table, event_id)
        except (live_features.EventNotFoundError, live_features.MalformedEventError, model_loader.NoPromotedModelError) as exc:
            prediction_cache.put_error_cached(s3, cache_key, type(exc).__name__, str(exc))
            return
        model_versions = prediction_cache.current_model_versions(s3, SPORT, model_versions_for(result["event_type"]))
        prediction_cache.put_cached(s3, cache_key, result, model_versions, result.get("status"))
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)
