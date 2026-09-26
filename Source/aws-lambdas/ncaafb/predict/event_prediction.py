"""
Prediction logic for one NCAAFB event or player-prop target, and the
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
from library.serving.ncaafb_reads import SCORE_MODELS, WIN_PROBABILITY_MODEL
from library.storage import prediction_cache

logger = logging.getLogger("ncaafb-predict")

SPORT = "ncaafb"

# Stat(s) each leader category needs scored.
LEADER_CATEGORY_STATS = {
    "passing": ["passing_yards", "passing_touchdowns"],
    "rushing": ["rushing_yards", "rushing_touchdowns"],
    "receiving": ["receiving_yards", "receiving_touchdowns"],
    "sacks": ["defensive_sacks"],
}

model_name_to_prop = common.model_name_to_prop
non_negative = common.non_negative
reconcile_scores = common.reconcile_scores
record_prediction = common.record_prediction


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Loads each distinct model at most once per request."""
    return common.get_cached_model(model_cache, s3, SPORT, model_name)


def _score_and_record_leader(storage, s3, predictions_table, model_cache: dict, event_key_value: str, feature_row: dict, stats: list[str]) -> dict:
    return common._score_and_record_leader(storage, s3, predictions_table, SPORT, model_cache, event_key_value, feature_row, stats)


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str, events: list[dict] | None = None) -> dict | None:
    """The `leaders` block -- passing/rushing/receiving/sacks leaders per team (passing
    singular, the rest lists -- see live_features.LEADER_CANDIDATE_LIMITS). Best-effort:
    a failure here is logged and returns None rather than failing predict_event."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value, events=events)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def score(feature_row: dict, stats: list[str]) -> dict:
        return _score_and_record_leader(storage, s3, predictions_table, model_cache, event_key_value, feature_row, stats)

    def ranked(team_candidates: dict, category: str) -> list[dict]:
        """Every candidate in this category, scored, sorted by its own
        predicted primary stat -- candidates arrive ordered by recent
        volume (how build_live_event_leader_candidates picked who to
        score at all), which can genuinely disagree with this game's own
        predicted value. A candidate missing its primary stat entirely
        (model not promoted) sorts last rather than crashing."""
        primary_stat = LEADER_CATEGORY_STATS[category][0]
        scored = [score(row, LEADER_CATEGORY_STATS[category]) for row in team_candidates[category]]
        scored.sort(key=lambda result: result.get(primary_stat, float("-inf")), reverse=True)
        return scored

    def team_leaders(team_candidates: dict) -> dict:
        passing_rows = team_candidates["passing"]
        return {
            "passing": score(passing_rows[0], LEADER_CATEGORY_STATS["passing"]) if passing_rows else None,
            "rushing": ranked(team_candidates, "rushing"),
            "receiving": ranked(team_candidates, "receiving"),
            "sacks": ranked(team_candidates, "sacks"),
        }

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


def snapshot_event(storage, s3, predictions_table, event_id: str) -> int:
    """Pre-kickoff snapshot for one event -- see common.snapshot_event."""
    return common.snapshot_event(storage, s3, predictions_table, event_id, SPORT, predict_event)


def compute_and_cache_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> None:
    """Same role as compute_and_cache_event, for one player-prop stat."""
    common.compute_and_cache_player_prop(
        storage, s3, predictions_table, event_id, entity_id, target_stat, SPORT, predict_player_prop,
    )
