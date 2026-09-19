"""
Prediction logic for one NCAA MBB event or player-prop target, and the
compute_and_cache_* background workers that populate
library.storage.prediction_cache on a cache miss. Byte-for-byte the same
shape as nba/predict/event_prediction.py -- basketball's leader
categories (scoring/rebounding/assists) are identical for both sports.
"""
import logging
from datetime import datetime, timezone

import live_features
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.serving import event_prediction_common as common
from library.serving import model_loader
from library.serving.ncaambb_reads import SCORE_MODELS, WIN_PROBABILITY_MODEL
from library.storage import prediction_cache

logger = logging.getLogger("ncaambb-predict")

SPORT = "ncaambb"

# Stat(s) each leader category needs scored. Matches
# library.serving.ncaambb_reads' own _STAT_CATEGORY, inverted.
LEADER_CATEGORY_STATS = {
    "scoring": ["points"],
    "rebounding": ["rebounds"],
    "assists": ["assists"],
}

model_name_to_prop = common.model_name_to_prop
non_negative = common.non_negative
reconcile_scores = common.reconcile_scores
record_prediction = common.record_prediction


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Loads each distinct model at most once per request."""
    return common.get_cached_model(model_cache, s3, SPORT, model_name)


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
            scored.sort(key=lambda row, primary_stat=primary_stat: row.get(primary_stat, float("-inf")), reverse=True)
            result[category] = scored
        return result

    return {"home": team_leaders(candidates["home"]), "away": team_leaders(candidates["away"])}


def predict_event(storage, s3, predictions_table, event_id: str) -> dict:
    return common.predict_event(
        storage, s3, predictions_table, event_id, SPORT, SCORE_MODELS, WIN_PROBABILITY_MODEL, predict_event_leaders,
    )


def predict_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> dict:
    return common.predict_player_prop(storage, s3, predictions_table, event_id, entity_id, target_stat, SPORT)


def compute_and_cache_event(storage, s3, predictions_table, event_id: str) -> None:
    """Background worker triggered by predict-read on a cache miss/stale-refresh. Computes
    predict_event and writes the result to the S3 prediction cache. A recognized,
    possibly-transient error (event not ingested, no model promoted) gets a short-lived
    negative cache entry; any other exception propagates after the in-progress claim clears."""
    common.compute_and_cache_event(storage, s3, predictions_table, event_id, SPORT, predict_event)


def compute_and_cache_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> None:
    """Same role as compute_and_cache_event, for one player-prop stat."""
    common.compute_and_cache_player_prop(
        storage, s3, predictions_table, event_id, entity_id, target_stat, SPORT, predict_player_prop,
    )
