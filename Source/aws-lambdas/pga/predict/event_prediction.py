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
import re
from collections import defaultdict
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

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
    rounds_fingerprint,
)
from library.storage import prediction_cache

logger = logging.getLogger("pga-predict")

SPORT = "pga"

# Matches this module's own _score-recorded model_key shape for a round
# model (MODEL#round-2#v3#GOLFER#1085) -- same style as nfl_reads.py's
# own _PLAYER_PROP_MODEL_KEY_RE. Captures the version too -- record_
# prediction's own stored `predicted_value` is just {"value": ...} (no
# model_version), unlike _score's return value, so the version has to be
# recovered from the model_key string itself.
_ROUND_MODEL_KEY_RE = re.compile(r"^MODEL#round-([1-4])#v(\d+)#GOLFER#(.+)$")


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
    """Real, already-stored result -- position/score_to_par/rounds all
    update at normalize time as the tournament progresses (confirmed
    live: ESPN's own status.position/score carry real current-standing
    values for an in-progress, not-yet-finished tournament, same as a
    completed one), so this is meaningful mid-tournament, not just once
    the whole event is "completed". `rounds` (each already-played round's
    own score_to_par/total_strokes, from _parse_rounds) is included
    whenever any exist, even if `finish_position`/`score_to_par` are both
    still None (the tournament's own top-level status can lag a per-round
    result by a normalize cycle) -- covers a golfer whose round is done
    but the cumulative summary hasn't refreshed yet."""
    result = participant.get("result") or {}
    rounds = result.get("rounds") or []
    if result.get("finish_position") is None and result.get("score_to_par") is None and not rounds:
        return None
    return {
        "finish_position": result.get("finish_position"),
        "score_to_par": result.get("score_to_par"),
        "rounds": rounds,
        # This golfer's own real ESPN status (scheduled/finished/cut/
        # made_cut_did_not_finish/withdrawn -- library/normalize/pga.py's
        # map_status), NOT derived from finish_position's presence. A real
        # current standing (finish_position/score_to_par) exists throughout
        # an in-progress tournament, well before this golfer's own round is
        # actually finished -- "has a position" was never a valid proxy
        # for "finished" (the frontend's own former fallback bug, fixed
        # alongside this: field_leaderboard_table.dart's STATUS column now
        # reads this field directly instead of inferring it).
        "status": result.get("status"),
    }


def _historical_round_predictions(predictions_table, event_key_value: str) -> dict[str, dict[int, dict]]:
    """{entity_id: {round_number: {"value":..., "model_version":...}}} --
    recovers a round's own PRE-round forecast even after live_features.py's
    applicable_rounds has since moved past it. A played round is never
    re-scored (applicable_rounds only ever returns rounds still ahead of
    a golfer), so the predictions_table item _score originally wrote for
    it -- before that round started -- is never overwritten; this just
    reads it back. One Query per event (predictions_table's only key is
    event_key/model_key, no per-golfer GSI -- same "one query, filter
    client-side" precedent nfl_reads.py's own _leaders_comparison uses for
    its player-prop rows), not one per golfer."""
    by_golfer: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in predictions_table.query(Key("event_key").eq(event_key_value)):
        match = _ROUND_MODEL_KEY_RE.match(row["model_key"])
        if match is None:
            continue
        round_number, model_version, entity_id = int(match.group(1)), int(match.group(2)), match.group(3)
        # Same {"value", "model_version"} shape _score's own return value
        # has -- the frontend's ModelValue.fromJson requires both.
        by_golfer[entity_id][round_number] = {"value": row["predicted_value"]["value"], "model_version": model_version}
    return by_golfer


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
    # Only queried once at all when at least one golfer has a played round
    # -- the common pre-tournament case (nobody's played anything yet) has
    # nothing to backfill, so skip the extra DynamoDB Query entirely then.
    any_round_played = any((p.get("result") or {}).get("rounds") for p in participants_by_id.values())
    historical_rounds = _historical_round_predictions(predictions_table, event_key_value) if any_round_played else {}

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
        # Backfill each already-played round's own PRE-round forecast so
        # the frontend's ROUND 1-4 breakdown can show what was originally
        # projected for a round next to its own real actual, not just for
        # whichever rounds are still ahead (a real user complaint -- round
        # 1's own projection used to vanish the moment round 1 was played,
        # since applicable_rounds no longer scores it going forward).
        participant = participants_by_id.get(entity_id)
        played_rounds = {r["round"] for r in ((participant or {}).get("result") or {}).get("rounds", [])}
        for round_number in played_rounds:
            historical = historical_rounds.get(entity_id, {}).get(round_number)
            if historical is not None:
                round_predictions.setdefault(f"round_{round_number}", historical)
        if round_predictions:
            predictions["rounds"] = round_predictions

        entry = {"entity_id": entity_id, **_golfer_name(storage, entity_id), "predictions": predictions}
        # Not gated on status == "completed" -- a real, already-stored
        # result (current standing, or a completed round) is meaningful
        # throughout the tournament, not just once it's fully over. See
        # _actual_golfer_result's own docstring.
        if participant is not None:
            actual = _actual_golfer_result(participant)
            if actual is not None:
                entry["actual"] = actual
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
        # A fresh fetch (not reused from predict_event's own internal one)
        # -- cheap (a single DynamoDB GetItem), and guarantees the
        # fingerprint reflects exactly the event state this prediction
        # was computed against, same as _score's own record_prediction
        # calls already do per-model. See pga_reads.rounds_fingerprint's
        # own docstring for why this exists.
        event = storage.get_event(event_key_value)
        extra_fingerprint = rounds_fingerprint(event) if event is not None else None
        prediction_cache.put_cached(s3, cache_key, result, model_versions, result.get("status"), extra_fingerprint)
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)
