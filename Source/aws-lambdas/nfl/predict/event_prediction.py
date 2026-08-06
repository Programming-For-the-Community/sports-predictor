"""
Per-request live prediction logic for GET /nfl/predictions/events/{event_id}
and GET /nfl/predictions/events/{event_id}/players/{entity_id} -- the two
on-demand routes handler.py's lambda_handler serves directly. Split out of
handler.py to keep the request-routing entry point focused on routing;
season_projection.py's weekly leaderboard scoring reuses _get_cached_model/
model_name_to_prop from here for the same per-stat model lookups.

Every function here takes storage/s3/predictions_table as parameters
rather than holding its own singleton, same convention live_features.py
and model_loader.py already use.
"""
import logging
from datetime import datetime, timezone

import live_features
import model_loader
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.serving.nfl_reads import SCORE_MODELS, WIN_PROBABILITY_MODEL

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


def model_name_to_prop(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def record_prediction(predictions_table, event_key_value: str, model_key: str, value) -> None:
    predictions_table.put_item({
        "event_key": event_key_value,
        "model_key": model_key,
        "predicted_value": value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_cached_model(model_cache: dict, s3, model_name: str):
    """Several leader candidates within the same request often need the
    SAME model (e.g. every receiver candidate needs
    player-prop-receiving-yards) -- this loads each distinct model at
    most once per request instead of once per candidate."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, SPORT, model_name)
    return model_cache[model_name]


def _score_leader_candidate(
    storage, s3, predictions_table, model_cache: dict, feature_row: dict, stats: list[str], event_key_value: str,
) -> dict:
    entity_id = feature_row["entity_id"]
    entity = storage.get_entity(SPORT, entity_id)
    result = {"entity_id": entity_id}
    if entity and entity.get("name"):
        result["name"] = entity["name"]

    for stat in stats:
        model_name = model_name_to_prop(stat)
        try:
            booster, model_card = get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            # This one stat's model hasn't been promoted yet -- leave it
            # out of the candidate's result rather than failing the whole
            # event prediction over a gap in an enhancement field.
            continue
        value = model_loader.predict(booster, model_card, feature_row)
        result[stat] = value
        # Same model_key shape predict_player_prop already writes for a
        # manually-queried single player+stat -- makes a leader's
        # prediction and a manual one interchangeable in the audit trail,
        # which is what lets nfl_reads._leaders_comparison read either
        # kind back for a completed event later. Best-effort like every
        # other call in this function: a write failure here shouldn't take
        # down the leaders block over what's purely an audit-trail concern.
        try:
            record_prediction(
                predictions_table, event_key_value,
                f"MODEL#{model_name}#v{model_card['version']}#PLAYER#{entity_id}", {"value": value},
            )
        except Exception:
            logger.exception("Failed recording leader prediction for %s/%s", entity_id, stat)
    return result


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str) -> dict | None:
    """The `leaders` block -- passing/receiving/rushing/sacks leaders per
    team. Best-effort: any failure here is logged and swallowed rather
    than propagated, since the core win/margin/score predictions in
    predict_event already succeeded by the time this runs and shouldn't
    be thrown away over a problem in this purely additive field."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def team_leaders(team_candidates: dict) -> dict:
        passing = team_candidates["passing"]
        return {
            "passing": _score_leader_candidate(
                storage, s3, predictions_table, model_cache, passing[0],
                LEADER_CATEGORY_STATS["passing"], event_key_value,
            ) if passing else None,
            "receiving": [
                _score_leader_candidate(
                    storage, s3, predictions_table, model_cache, row,
                    LEADER_CATEGORY_STATS["receiving"], event_key_value,
                )
                for row in team_candidates["receiving"]
            ],
            "rushing": [
                _score_leader_candidate(
                    storage, s3, predictions_table, model_cache, row,
                    LEADER_CATEGORY_STATS["rushing"], event_key_value,
                )
                for row in team_candidates["rushing"]
            ],
            "sacks": [
                _score_leader_candidate(
                    storage, s3, predictions_table, model_cache, row,
                    LEADER_CATEGORY_STATS["sacks"], event_key_value,
                )
                for row in team_candidates["sacks"]
            ],
        }

    return {"home": team_leaders(candidates["home"]), "away": team_leaders(candidates["away"])}


def predict_event(storage, s3, predictions_table, event_id: str) -> dict:
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_event_features(storage, SPORT, event_key_value)

    booster, model_card = model_loader.load_current_model(s3, SPORT, WIN_PROBABILITY_MODEL)
    home_win_probability = model_loader.predict(booster, model_card, feature_row)
    predictions = {
        "win_probability": {"home_win_probability": home_win_probability, "model_version": model_card["version"]},
    }
    record_prediction(
        predictions_table, event_key_value,
        f"MODEL#{WIN_PROBABILITY_MODEL}#v{model_card['version']}", predictions["win_probability"],
    )

    for target, model_name in SCORE_MODELS.items():
        booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
        value = model_loader.predict(booster, model_card, feature_row)
        predictions[target] = {"value": value, "model_version": model_card["version"]}
        record_prediction(predictions_table, event_key_value, f"MODEL#{model_name}#v{model_card['version']}", predictions[target])

    return {
        "event_key": event_key_value,
        "predictions": predictions,
        "leaders": predict_event_leaders(storage, s3, predictions_table, event_key_value),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def predict_player_prop(storage, s3, predictions_table, event_id: str, entity_id: str, target_stat: str) -> dict:
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_player_features(storage, SPORT, event_key_value, entity_id)

    model_name = model_name_to_prop(target_stat)
    booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
    value = model_loader.predict(booster, model_card, feature_row)

    entity_key_value = build_entity_key(SPORT, entity_id)
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
