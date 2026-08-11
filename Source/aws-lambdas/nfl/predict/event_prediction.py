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

# How many make the leaders block per category -- matches
# team_leaders_panel.dart's own documented shape ("top 3 receivers, top 2
# rushers, top 3 in sacks"). live_features._team_leader_candidates
# intentionally leaves the RECEIVING candidate pool UNCAPPED (every
# eligible player across every depth-chart slot -- WR/TE/RB/QB, see that
# function's own docstring) specifically so THIS is where the actual
# per-candidate model predictions do the ranking, not depth-chart rank.
# Also enforced for rushing/sacks here even though their own candidate
# generation already caps them, so this stays the one place that decides
# "how many make the leaders block" rather than depending on two layers
# staying in sync.
LEADER_CATEGORY_LIMITS = {"receiving": 3, "rushing": 2, "sacks": 3}


def model_name_to_prop(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def non_negative(value: float) -> float:
    """Floors a regression prediction at 0 -- points, sacks, and every
    player-prop stat are counting stats and can't be negative, but
    ElasticNet in particular can output one for a low-volume player. Not
    applied to margin, which is a signed value."""
    return max(0.0, value)


def reconcile_scores(margin: float, home_score: float, away_score: float) -> dict[str, float]:
    """margin/home_score/away_score are three independently trained
    regressors -- nothing constrains raw home_score - away_score to equal
    the raw margin prediction. Splits the discrepancy evenly between
    home_score and away_score, preserving their combined total, so the
    reconciled pair agrees exactly with margin instead of the two
    predictions silently disagreeing on which team is even favored."""
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
    """Several leader candidates within the same request often need the
    SAME model (e.g. every receiver candidate needs
    player-prop-receiving-yards) -- this loads each distinct model at
    most once per request instead of once per candidate."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, SPORT, model_name)
    return model_cache[model_name]


def _score_leader_candidate(s3, model_cache: dict, feature_row: dict, stats: list[str]) -> dict:
    """Predicted value per stat for one leader candidate -- {"entity_id",
    stat: value, ...}, missing a stat key entirely when that stat's model
    hasn't been promoted yet (see the NoPromotedModelError catch below).
    Scoring only: no predictions-table write and no entity-name lookup --
    both are deferred to the candidates that actually survive ranking
    (see team_leaders below), since scoring itself runs over the full,
    often much larger, unranked candidate pool (receiving in particular is
    deliberately uncapped upstream -- see live_features.
    _team_leader_candidates' own docstring)."""
    result = {"entity_id": feature_row["entity_id"]}
    for stat in stats:
        model_name = model_name_to_prop(stat)
        try:
            booster, model_card = get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            # This one stat's model hasn't been promoted yet -- leave it
            # out of the candidate's result rather than failing the whole
            # event prediction over a gap in an enhancement field.
            continue
        result[stat] = non_negative(model_loader.predict(booster, model_card, feature_row))
    return result


def _record_leader_predictions(
    storage, s3, predictions_table, model_cache: dict, event_key_value: str, scored: dict,
) -> dict:
    """Writes the audit-trail prediction row for every stat in `scored`
    (an already-ranked candidate's _score_leader_candidate result) and
    attaches its display name -- called only for candidates that made the
    final cut (see team_leaders below), so the predictions-table audit
    trail (and, downstream, nfl_reads._leaders_comparison's predicted-vs-
    actual read of it) only ever contains the players actually shown in
    the leaders block, not the whole unranked pool scoring considered."""
    entity_id = scored["entity_id"]
    entity = storage.get_entity(SPORT, entity_id)
    result = dict(scored)
    if entity and entity.get("name"):
        result["name"] = entity["name"]

    for stat, value in scored.items():
        if stat == "entity_id":
            continue
        model_name = model_name_to_prop(stat)
        # Already in model_cache from this same candidate's own scoring
        # pass -- this can only re-raise NoPromotedModelError if the stat
        # somehow made it into `scored` without a successful load, which
        # _score_leader_candidate never does.
        _, model_card = get_cached_model(model_cache, s3, model_name)
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


def predict_event_leaders(storage, s3, predictions_table, event_key_value: str, events: list[dict] | None = None) -> dict | None:
    """The `leaders` block -- passing/receiving/rushing/sacks leaders per
    team. Best-effort: any failure here is logged and swallowed rather
    than propagated, since the core win/margin/score predictions in
    predict_event already succeeded by the time this runs and shouldn't
    be thrown away over a problem in this purely additive field. `events`
    is predict_event's own already-fetched get_all_events result, passed
    through so this doesn't pay for a second one -- see live_features.
    build_live_event_leader_candidates' own docstring."""
    try:
        candidates = live_features.build_live_event_leader_candidates(storage, SPORT, event_key_value, events=events)
    except Exception:
        logger.exception("Failed to build leader candidates for %s", event_key_value)
        return None

    model_cache: dict = {}

    def ranked(team_candidates: dict, category: str) -> list[dict]:
        """Every candidate in this category, scored, ranked by its
        primary stat (yards for receiving/rushing, sacks for sacks) and
        cut down to LEADER_CATEGORY_LIMITS[category] -- a candidate
        missing its primary stat entirely (model not promoted) sorts
        last, not first, so an unpromoted model can't accidentally crowd
        out real predictions."""
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
    event_key_value = build_event_key(SPORT, event_id)
    # Fetched ONCE and threaded through both build_live_event_features and
    # predict_event_leaders below -- see live_features.py's own docstring
    # for why a naive per-call fetch (the pre-existing behavior omitted
    # `events` still falls back to) makes this route pay for 8 full-
    # history queries instead of 1.
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


def compute_and_cache_event(storage, s3, predictions_table, event_id: str) -> None:
    """Background worker for a cache miss/stale-refresh on GET /nfl/
    predictions/events/{event_id} -- triggered asynchronously by predict-
    read/handler.py (never by API Gateway directly, so this isn't bound
    by its 29s ceiling; only this Lambda's own 300s timeout applies).
    Computes predict_event's own result (same function, same audit-trail
    writes) and additionally writes it to the S3 prediction cache
    (library.storage.prediction_cache) so the next request can be served
    instantly instead of triggering another compute.

    A failure with one of prediction_cache.ERROR_STATUS_CODES' recognized,
    possibly-transient errors (event_id not ingested yet, no model
    promoted yet) gets a short-lived negative cache entry instead of
    leaving the request stuck at "computing" forever -- see
    prediction_cache.put_error_cached's own docstring. Any OTHER
    exception is deliberately not caught here -- it propagates after the
    in-progress claim is still cleared (see the finally below), shows up
    as a real Lambda error, same "let it show up, don't silently reshape
    it" reasoning season_projection.run_scheduled's own docstring gives."""
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
    """Background worker for a cache miss/stale-refresh on GET /nfl/
    predictions/events/{event_id}/players/{entity_id}?stat=... -- same
    role, and same recognized-error-vs-real-bug handling, as
    compute_and_cache_event."""
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
