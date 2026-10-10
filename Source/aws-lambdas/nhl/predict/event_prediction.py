"""
Prediction logic for one NHL event or player-prop target, and the
compute_and_cache_* background workers that populate
library.storage.prediction_cache on a cache miss.

An event prediction also carries `goalies`: the starting goalie each
side's numbers were computed for, {"home": {"entity_id", "name",
"source"}, "away": {...}}, where source is "confirmed", "probable" or
"predicted" (library.features.hockey_live). The graded prediction is the
snapshot the prediction scheduler takes shortly after puck drop, by which
time the starters are confirmed; anything computed earlier is provisional.
"""
import logging

import live_features
from library.serving import event_prediction_common as common
from library.serving.nhl_reads import (
    CATEGORY_PRIMARY_STAT,
    GOALIE_CATEGORY,
    LEADER_CATEGORY_LIMITS,
    LEADER_CATEGORY_STATS,
    SCORE_MODELS,
    WIN_PROBABILITY_MODEL,
)

logger = logging.getLogger("nhl-predict")

SPORT = "nhl"

model_name_to_prop = common.model_name_to_prop
non_negative = common.non_negative
reconcile_scores = common.reconcile_scores
record_prediction = common.record_prediction


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Loads each distinct model at most once per request."""
    return common.get_cached_model(model_cache, s3, SPORT, model_name)


def _team_leaders(score, candidates: dict) -> dict:
    leaders = {}
    for category, stats in LEADER_CATEGORY_STATS.items():
        if category == GOALIE_CATEGORY:
            rows = [candidates["goalie"]] if candidates.get("goalie") else []
        else:
            rows = candidates["skaters"]
        scored = [score(row, stats) for row in rows]
        primary_stat = CATEGORY_PRIMARY_STAT[category]
        scored.sort(key=lambda entry, primary_stat=primary_stat: entry.get(primary_stat, float("-inf")), reverse=True)
        leaders[category] = scored[:LEADER_CATEGORY_LIMITS[category]]
    return leaders


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str, events: list[dict] | None = None) -> dict | None:
    """The `leaders` block -- scoring/shooting/physical leaders among each
    team's most-used skaters, and the starting goalie under goaltending.
    Every category is a list. Best-effort: a failure is logged and
    returns None rather than failing predict_event."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value, events=events)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def score(feature_row: dict, stats: list[str]) -> dict:
        return common._score_and_record_leader(
            storage, s3, predictions_table, SPORT, model_cache, event_key_value, feature_row, stats,
        )

    return {"home": _team_leaders(score, candidates["home"]), "away": _team_leaders(score, candidates["away"])}


def _named_goalies(storage, event_key_value: str) -> dict:
    goalies = {}
    for role, goalie in live_features.resolved_goalies(event_key_value).items():
        entity = storage.get_entity(SPORT, goalie["entity_id"], "player")
        goalies[role] = {**goalie, "name": (entity or {}).get("name")}
    return goalies


def predict_event(storage, s3, predictions_table, event_id: str) -> dict:
    result = common.predict_event(
        storage, s3, predictions_table, event_id, SPORT, SCORE_MODELS, WIN_PROBABILITY_MODEL, predict_event_leaders,
    )
    result["goalies"] = _named_goalies(storage, result["event_key"])
    return result


_entry_points = common.HeadToHeadEntryPoints(globals(), SPORT, SCORE_MODELS, WIN_PROBABILITY_MODEL)
predict_player_prop = _entry_points.predict_player_prop
compute_and_cache_event = _entry_points.compute_and_cache_event
snapshot_event = _entry_points.snapshot_event
compute_and_cache_player_prop = _entry_points.compute_and_cache_player_prop
