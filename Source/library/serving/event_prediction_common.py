"""
Shared predict_event/predict_player_prop core and the compute_and_cache_*
background workers that populate library.storage.prediction_cache on a
cache miss -- common to nfl/nba/ncaafb/ncaambb's own predict/
event_prediction.py. Each sport's own module keeps its own leader-selection
logic (the shape of `leaders` differs per sport) and calls into here with
its own SPORT/SCORE_MODELS/WIN_PROBABILITY_MODEL config plus its own
predict_event_leaders/predict_event/predict_player_prop as callables, so
that a test patching e.g. `event_prediction.predict_event_leaders` still
takes effect (the lookup happens in each sport's own module globals, not
baked in here).
"""
import logging
from datetime import datetime, timezone

import live_features
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.serving import model_loader
from library.storage import prediction_cache

logger = logging.getLogger("event-prediction-common")


def model_name_to_prop(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def non_negative(value: float) -> float:
    """Floors a regression prediction at 0 -- not applied to margin, which is signed."""
    return max(0.0, value)


def reconcile_scores(margin: float, home_score: float, away_score: float) -> dict[str, float]:
    """Splits the margin/home_score/away_score discrepancy evenly, preserving the combined total."""
    adjustment = ((home_score - away_score) - margin) / 2
    return {
        "margin": margin,
        "home_score": non_negative(home_score - adjustment),
        "away_score": non_negative(away_score + adjustment),
    }


def record_prediction(predictions_table, event_key_value: str, model_key: str, value) -> None:
    predictions_table.put_item({
        "event_key": event_key_value,
        "model_key": model_key,
        "predicted_value": value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_cached_model(model_cache: dict, s3, sport: str, model_name: str):
    """Loads each distinct model at most once per request."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, sport, model_name)
    return model_cache[model_name]


def predict_event(
    storage, s3, predictions_table, event_id: str, sport: str,
    score_models: dict, win_probability_model: str, leaders_fn,
) -> dict:
    event_key_value = build_event_key(sport, event_id)
    events = storage.get_all_events(sport)
    feature_row = live_features.build_live_event_features(storage, sport, event_key_value, events=events)

    booster, model_card = model_loader.load_current_model(s3, sport, win_probability_model)
    home_win_probability = model_loader.predict(booster, model_card, feature_row)
    predictions = {
        "win_probability": {"home_win_probability": home_win_probability, "model_version": model_card["version"]},
    }
    record_prediction(
        predictions_table, event_key_value,
        f"MODEL#{win_probability_model}#v{model_card['version']}", predictions["win_probability"],
    )

    raw_values = {}
    score_model_cards = {}
    for target, model_name in score_models.items():
        booster, model_card = model_loader.load_current_model(s3, sport, model_name)
        raw_values[target] = model_loader.predict(booster, model_card, feature_row)
        score_model_cards[target] = model_card

    reconciled = reconcile_scores(raw_values["margin"], raw_values["home_score"], raw_values["away_score"])
    for target, model_name in score_models.items():
        model_card = score_model_cards[target]
        predictions[target] = {"value": reconciled[target], "model_version": model_card["version"]}
        record_prediction(predictions_table, event_key_value, f"MODEL#{model_name}#v{model_card['version']}", predictions[target])

    return {
        "event_key": event_key_value,
        "predictions": predictions,
        "leaders": leaders_fn(storage, s3, predictions_table, event_key_value, events=events),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def predict_player_prop(
    storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str, sport: str,
) -> dict:
    event_key_value = build_event_key(sport, event_id)
    feature_row = live_features.build_live_player_features(storage, sport, event_key_value, entity_id)

    model_name = model_name_to_prop(target_stat)
    booster, model_card = model_loader.load_current_model(s3, sport, model_name)
    value = non_negative(model_loader.predict(booster, model_card, feature_row))

    entity_key_value = build_entity_key(sport, entity_id, "player")
    record_prediction(
        predictions_table, event_key_value,
        f"MODEL#{model_name}#v{model_card['version']}#PLAYER#{entity_id}", {"value": value},
    )

    return {
        "event_key": event_key_value,
        "entity_key": entity_key_value,
        "stat": target_stat,
        "prediction": {"value": value, "model_version": model_card["version"]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_and_cache_event(storage, s3, predictions_table, event_id: str, sport: str, predict_event_fn) -> None:
    """Background worker triggered by predict-read on a cache miss/stale-refresh. Calls
    predict_event_fn (each sport's own predict_event) and writes the result to the S3
    prediction cache. A recognized, possibly-transient error (event not ingested, no model
    promoted) gets a short-lived negative cache entry; any other exception propagates
    after the in-progress claim clears."""
    event_key_value = build_event_key(sport, event_id)
    cache_key = prediction_cache.event_prediction_cache_key(sport, event_key_value)
    try:
        try:
            result = predict_event_fn(storage, s3, predictions_table, event_id)
        except (live_features.EventNotFoundError, live_features.MalformedEventError, model_loader.NoPromotedModelError) as exc:
            prediction_cache.put_error_cached(s3, cache_key, type(exc).__name__, str(exc))
            return
        event = storage.get_event(event_key_value)
        model_versions = {key: result["predictions"][key]["model_version"] for key in prediction_cache.CORE_EVENT_MODELS}
        prediction_cache.put_cached(s3, cache_key, result, model_versions, (event or {}).get("status"))
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)


def compute_and_cache_player_prop(
    storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str, sport: str, predict_player_prop_fn,
) -> None:
    """Same role as compute_and_cache_event, for one player-prop stat."""
    event_key_value = build_event_key(sport, event_id)
    cache_key = prediction_cache.player_prop_cache_key(sport, event_key_value, entity_id, target_stat)
    try:
        try:
            result = predict_player_prop_fn(storage, s3, predictions_table, event_id, entity_id, target_stat)
        except (live_features.EventNotFoundError, live_features.MalformedEventError, model_loader.NoPromotedModelError) as exc:
            prediction_cache.put_error_cached(s3, cache_key, type(exc).__name__, str(exc))
            return
        event = storage.get_event(event_key_value)
        model_version = result["prediction"]["model_version"]
        prediction_cache.put_cached(s3, cache_key, result, model_version, (event or {}).get("status"))
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)
