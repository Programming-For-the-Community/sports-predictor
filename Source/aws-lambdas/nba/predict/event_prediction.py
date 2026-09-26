"""
Prediction logic for one NBA event or player-prop target, and the
compute_and_cache_* background workers that populate
library.storage.prediction_cache on a cache miss.
"""
import logging
from datetime import datetime, timezone

import live_features
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.serving import event_prediction_common as common
from library.serving import model_loader
from library.serving.nba_reads import SCORE_MODELS, WIN_PROBABILITY_MODEL
from library.storage import prediction_cache

logger = logging.getLogger("nba-predict")

SPORT = "nba"

# Read directly by season_projection.py's own leaderboard-candidate loop.
LEADER_CATEGORY_STATS = common.BASKETBALL_LEADER_CATEGORY_STATS

model_name_to_prop = common.model_name_to_prop
non_negative = common.non_negative
reconcile_scores = common.reconcile_scores
record_prediction = common.record_prediction


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Loads each distinct model at most once per request."""
    return common.get_cached_model(model_cache, s3, SPORT, model_name)


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str, events: list[dict] | None = None) -> dict | None:
    """The `leaders` block -- scoring/rebounding/assists leaders per team,
    each always a list (no leader is inherently singular in basketball)."""
    return common.basketball_predict_event_leaders(storage, s3, predictions_table, event_key_value, SPORT, events=events)


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


def snapshot_event(storage, s3, predictions_table, event_id: str) -> int:
    """Pre-kickoff snapshot for one event -- see common.snapshot_event."""
    return common.snapshot_event(storage, s3, predictions_table, event_id, SPORT, predict_event)


def compute_and_cache_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> None:
    """Same role as compute_and_cache_event, for one player-prop stat."""
    common.compute_and_cache_player_prop(
        storage, s3, predictions_table, event_id, entity_id, target_stat, SPORT, predict_player_prop,
    )
