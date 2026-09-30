"""
Prediction logic for one NCAA MBB event or player-prop target, and the
compute_and_cache_* background workers that populate
library.storage.prediction_cache on a cache miss. Leader scoring itself
(scoring/rebounding/assists, identical for nba/ncaambb) lives in
library.serving.event_prediction_common.basketball_predict_event_leaders.
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


_entry_points = common.HeadToHeadEntryPoints(globals(), SPORT, SCORE_MODELS, WIN_PROBABILITY_MODEL)
predict_event = _entry_points.predict_event
predict_player_prop = _entry_points.predict_player_prop
compute_and_cache_event = _entry_points.compute_and_cache_event
snapshot_event = _entry_points.snapshot_event
compute_and_cache_player_prop = _entry_points.compute_and_cache_player_prop
