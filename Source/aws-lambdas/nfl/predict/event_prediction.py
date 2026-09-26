"""
Per-request live prediction logic for GET /nfl/predictions/events/{event_id}
and GET /nfl/predictions/events/{event_id}/players/{entity_id}.

Every function here takes storage/s3/predictions_table as parameters
rather than holding its own singleton.
"""
import logging
from datetime import datetime, timezone

import live_features
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.serving import event_prediction_common as common
from library.serving import model_loader
from library.serving.nfl_reads import SCORE_MODELS, WIN_PROBABILITY_MODEL
from library.storage import prediction_cache

logger = logging.getLogger("nfl-predict")

SPORT = "nfl"

# Which player-prop stat(s) each leader category needs scored -- passing
# and receiving/rushing candidates each need two related stats (e.g. a
# QB's yards AND touchdowns), sacks needs just the one.
LEADER_CATEGORY_STATS = {
    "passing": ["passing_yards", "passing_touchdowns"],
    "receiving": ["receiving_yards", "receiving_touchdowns"],
    "rushing": ["rushing_yards", "rushing_touchdowns"],
    "sacks": ["defensive_sacks"],
}

# How many make the leaders block per category: top 3 receivers, top 2
# rushers, top 3 in sacks.
LEADER_CATEGORY_LIMITS = {"receiving": 3, "rushing": 2, "sacks": 3}


model_name_to_prop = common.model_name_to_prop
non_negative = common.non_negative
reconcile_scores = common.reconcile_scores
record_prediction = common.record_prediction


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Several leader candidates within the same request often need the
    SAME model (e.g. every receiver candidate needs
    player-prop-receiving-yards) -- this loads each distinct model at
    most once per request instead of once per candidate."""
    return common.get_cached_model(model_cache, s3, SPORT, model_name)


def _score_leader_candidate(s3, model_cache: dict, feature_row: dict, stats: list[str]) -> dict:
    """Predicted value per stat for one leader candidate -- {"entity_id",
    stat: value, ...}, missing a stat key entirely when that stat's model
    hasn't been promoted yet. Scoring only: no predictions-table write and
    no entity-name lookup -- both are deferred to the candidates that
    actually survive ranking (see team_leaders below)."""
    result = {"entity_id": feature_row["entity_id"]}
    for stat in stats:
        model_name = model_name_to_prop(stat)
        try:
            booster, model_card = get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            continue
        result[stat] = non_negative(model_loader.predict(booster, model_card, feature_row))
    return result


def _record_leader_predictions(
    storage, s3, predictions_table, model_cache: dict, event_key_value: str, scored: dict,
) -> dict:
    """Writes the audit-trail prediction row for every stat in `scored`
    (an already-ranked candidate's _score_leader_candidate result) and
    attaches its display name -- called only for candidates that made the
    final cut (see team_leaders below)."""
    entity_id = scored["entity_id"]
    entity = storage.get_entity(SPORT, entity_id, "player")
    result = dict(scored)
    if entity and entity.get("name"):
        result["name"] = entity["name"]

    for stat, value in scored.items():
        if stat == "entity_id":
            continue
        model_name = model_name_to_prop(stat)
        _, model_card = get_cached_model(model_cache, s3, model_name)
        # Best-effort: a write failure here shouldn't take down the
        # leaders block over what's purely an audit-trail concern.
        try:
            record_prediction(
                predictions_table, event_key_value,
                f"MODEL#{model_name}#v{model_card['version']}#PLAYER#{entity_id}", {"value": value},
            )
        except Exception:
            logger.exception("Failed recording leader prediction for %s/%s", entity_id, stat)
    return result


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str, events: list[dict] | None = None) -> dict | None:
    """The `leaders` block -- passing/receiving/rushing/sacks leaders per team. Best-effort:
    a failure here is logged and returns None rather than failing predict_event."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value, events=events)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def ranked(team_candidates: dict, category: str) -> list[dict]:
        """Every candidate in this category, scored, ranked by its
        primary stat (yards for receiving/rushing, sacks for sacks) and
        cut down to LEADER_CATEGORY_LIMITS[category]. A candidate missing
        its primary stat entirely (model not promoted) sorts last."""
        primary_stat = LEADER_CATEGORY_STATS[category][0]
        scored = [_score_leader_candidate(s3, model_cache, row, LEADER_CATEGORY_STATS[category])
                  for row in team_candidates[category]]
        scored.sort(key=lambda result: result.get(primary_stat, -1), reverse=True)
        winners = scored[:LEADER_CATEGORY_LIMITS[category]]
        return [_record_leader_predictions(storage, s3, predictions_table, model_cache, event_key_value, result)
                for result in winners]

    def team_leaders(team_candidates: dict) -> dict:
        passing = team_candidates["passing"]
        passing_result = None
        if passing:
            scored = _score_leader_candidate(s3, model_cache, passing[0], LEADER_CATEGORY_STATS["passing"])
            passing_result = _record_leader_predictions(storage, s3, predictions_table, model_cache, event_key_value, scored)
        return {
            "passing": passing_result,
            "receiving": ranked(team_candidates, "receiving"),
            "rushing": ranked(team_candidates, "rushing"),
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
