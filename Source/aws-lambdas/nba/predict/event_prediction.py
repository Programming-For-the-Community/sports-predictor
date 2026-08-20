"""
Prediction logic for one NBA event or player-prop target, and the
compute_and_cache_* background workers that populate
library.storage.prediction_cache on a cache miss.
"""
import logging
from datetime import datetime, timezone

import live_features
import model_loader
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.serving.nba_reads import SCORE_MODELS, WIN_PROBABILITY_MODEL
from library.storage import prediction_cache

logger = logging.getLogger("nba-predict")

SPORT = "nba"

# Stat(s) each leader category needs scored. Matches
# library.serving.nba_reads' own _STAT_CATEGORY, inverted.
LEADER_CATEGORY_STATS = {
    "scoring": ["points"],
    "rebounding": ["rebounds"],
    "assists": ["assists"],
}


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


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Loads each distinct model at most once per request."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, SPORT, model_name)
    return model_cache[model_name]


def _score_and_record_leader(storage, s3, predictions_table, model_cache: dict, event_key_value: str, feature_row: dict, stats: list[str]) -> dict:
    """Scores one leader candidate against every stat in `stats` and records each prediction.
    Missing a stat key entirely if that stat's model hasn't been promoted yet."""
    entity_id = feature_row["entity_id"]
    result = {"entity_id": entity_id}
    entity = storage.get_entity(SPORT, entity_id, "player")
    if entity and entity.get("name"):
        result["name"] = entity["name"]

    for stat in stats:
        model_name = model_name_to_prop(stat)
        try:
            booster, model_card = get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            continue
        value = non_negative(model_loader.predict(booster, model_card, feature_row))
        result[stat] = value
        try:
            record_prediction(
                predictions_table, event_key_value,
                f"MODEL#{model_name}#v{model_card['version']}#PLAYER#{entity_id}", {"value": value},
            )
        except Exception:
            logger.exception("Failed recording leader prediction for %s/%s", entity_id, stat)
    return result


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str, events: list[dict] | None = None) -> dict | None:
    """The `leaders` block -- scoring/rebounding/assists leaders per team,
    each always a list (no leader is inherently singular in basketball).
    Best-effort: a failure here is logged and returns None rather than
    failing predict_event."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value, events=events)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def score(feature_row: dict, stats: list[str]) -> dict:
        return _score_and_record_leader(storage, s3, predictions_table, model_cache, event_key_value, feature_row, stats)

    def team_leaders(team_candidates: dict) -> dict:
        # Candidates arrive ordered by recent volume (that's how
        # build_live_event_leader_candidates picks who to score at all),
        # not by this game's own predicted value -- the two can genuinely
        # disagree (e.g. a cold matchup for an otherwise-hot scorer), so
        # each category is re-sorted by its own scored stat before
        # returning, descending, missing-stat rows (no promoted model)
        # last rather than crashing.
        result = {}
        for category in LEADER_CATEGORY_STATS:
            primary_stat = LEADER_CATEGORY_STATS[category][0]
            scored = [score(row, LEADER_CATEGORY_STATS[category]) for row in team_candidates[category]]
            scored.sort(key=lambda row: row.get(primary_stat, float("-inf")), reverse=True)
            result[category] = scored
        return result

    return {"home": team_leaders(candidates["home"]), "away": team_leaders(candidates["away"])}


def predict_event(storage, s3, predictions_table, event_id: str) -> dict:
    event_key_value = build_event_key(SPORT, event_id)
    events = storage.get_all_events(SPORT)
    feature_row = live_features.build_live_event_features(storage, SPORT, event_key_value, events=events)

    booster, model_card = model_loader.load_current_model(s3, SPORT, WIN_PROBABILITY_MODEL)
    home_win_probability = model_loader.predict(booster, model_card, feature_row)
    predictions = {
        "win_probability": {"home_win_probability": home_win_probability, "model_version": model_card["version"]},
    }
    record_prediction(
        predictions_table, event_key_value,
        f"MODEL#{WIN_PROBABILITY_MODEL}#v{model_card['version']}", predictions["win_probability"],
    )

    raw_values = {}
    score_model_cards = {}
    for target, model_name in SCORE_MODELS.items():
        booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
        raw_values[target] = model_loader.predict(booster, model_card, feature_row)
        score_model_cards[target] = model_card

    reconciled = reconcile_scores(raw_values["margin"], raw_values["home_score"], raw_values["away_score"])
    for target, model_name in SCORE_MODELS.items():
        model_card = score_model_cards[target]
        predictions[target] = {"value": reconciled[target], "model_version": model_card["version"]}
        record_prediction(predictions_table, event_key_value, f"MODEL#{model_name}#v{model_card['version']}", predictions[target])

    return {
        "event_key": event_key_value,
        "predictions": predictions,
        "leaders": predict_event_leaders(storage, s3, predictions_table, event_key_value, events=events),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def predict_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> dict:
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_player_features(storage, SPORT, event_key_value, entity_id)

    model_name = model_name_to_prop(target_stat)
    booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
    value = non_negative(model_loader.predict(booster, model_card, feature_row))

    entity_key_value = build_entity_key(SPORT, entity_id, "player")
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


def compute_and_cache_event(storage, s3, predictions_table, event_id: str) -> None:
    """Background worker triggered by predict-read on a cache miss/stale-refresh. Computes
    predict_event and writes the result to the S3 prediction cache. A recognized,
    possibly-transient error (event not ingested, no model promoted) gets a short-lived
    negative cache entry; any other exception propagates after the in-progress claim clears."""
    event_key_value = build_event_key(SPORT, event_id)
    cache_key = prediction_cache.event_prediction_cache_key(SPORT, event_key_value)
    try:
        try:
            result = predict_event(storage, s3, predictions_table, event_id)
        except (live_features.EventNotFoundError, live_features.MalformedEventError, model_loader.NoPromotedModelError) as exc:
            prediction_cache.put_error_cached(s3, cache_key, type(exc).__name__, str(exc))
            return
        event = storage.get_event(event_key_value)
        model_versions = {key: result["predictions"][key]["model_version"] for key in prediction_cache.CORE_EVENT_MODELS}
        prediction_cache.put_cached(s3, cache_key, result, model_versions, (event or {}).get("status"))
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)


def compute_and_cache_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> None:
    """Same role as compute_and_cache_event, for one player-prop stat."""
    event_key_value = build_event_key(SPORT, event_id)
    cache_key = prediction_cache.player_prop_cache_key(SPORT, event_key_value, entity_id, target_stat)
    try:
        try:
            result = predict_player_prop(storage, s3, predictions_table, event_id, entity_id, target_stat)
        except (live_features.EventNotFoundError, live_features.MalformedEventError, model_loader.NoPromotedModelError) as exc:
            prediction_cache.put_error_cached(s3, cache_key, type(exc).__name__, str(exc))
            return
        event = storage.get_event(event_key_value)
        model_version = result["prediction"]["model_version"]
        prediction_cache.put_cached(s3, cache_key, result, model_version, (event or {}).get("status"))
    finally:
        prediction_cache.clear_in_progress(s3, cache_key)
