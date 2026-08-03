"""
NFL inference Lambda. Triggered by API Gateway (REST API, Lambda proxy
integration) behind the Cognito authorizer already wired in
Terraform/api-gateway.tf -- every request reaching this function has
already had its JWT validated by API Gateway itself; this code never
checks auth.

Routes (see Terraform/lambda-nfl-predict.tf for the API Gateway wiring):
    GET /nfl/predictions/events/{event_id}
        -> win probability, margin, home score, and away score for one
           upcoming/completed matchup, computed from one shared live
           feature vector (see live_features.build_live_event_features),
           plus a `leaders` block: presumptive passing/receiving/rushing/
           sacks leaders per team (see
           live_features.build_live_event_leader_candidates), each
           scored against the matching player-prop model. `leaders` is
           `null` if it couldn't be computed -- best-effort, never fails
           the core predictions above over it.
    GET /nfl/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards
        -> one player-prop prediction for one player in one game. `stat`
           must be one of the trained TARGET_STAT values (see
           Terraform/scheduler-nfl-train-player-prop-model.tf's
           nfl_player_prop_stats).
    GET /nfl/season
        -> the current season's standings (real record so far + Elo Monte
           Carlo projected final wins, division-winner/playoff/
           championship probability per team -- see season_simulation.py)
           and, per tracked player-prop stat, a top-10 season-long
           leaderboard projected from each candidate's current total plus
           their own model's prediction for their team's next game.
           `leaderboards` is `null` if it couldn't be computed --
           best-effort, same reasoning as `leaders` above.

{event_id}/{entity_id} are raw ESPN ids (e.g. "401547417",
"3139477"), not the internal SPORT#NFL#EVENT#... key -- translated via
library.schema.keys the same way every other NFL adapter does.

Every prediction is computed live on every request (no caching, no
serving a previously-computed value back) and logged to the predictions
table for the audit trail -- see design/DATA_SCHEMA.md's Predictions
table. The predictions table's IAM grant is PutItem-only (see
Terraform/iam-lambda-inference.tf); this function never reads a past
prediction back.
"""
from collections import Counter
import json
import logging
import os
from datetime import datetime, timezone

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.features.nfl import compute_elo_ratings
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.storage.feature_storage import FeatureStorage
from library.storage.model_artifacts import current_version_key, model_artifact_key
import live_features
import model_loader
import season_simulation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-predict")

SPORT = "nfl"
WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}

# Source of truth is Terraform/scheduler-nfl-train-player-prop-model.tf's
# nfl_player_prop_stats map -- duplicated here as a plain list (not read
# from Terraform at runtime) since there's no DynamoDB-backed model
# registry yet (design/PROJECT_PLAN.md Phase 4) to read it from instead.
PLAYER_PROP_STATS = [
    "passing_yards", "passing_touchdowns", "rushing_yards", "rushing_touchdowns",
    "receiving_yards", "receiving_touchdowns", "defensive_sacks",
]

# Which player-prop stat(s) each leader category needs scored -- passing
# and receiving/rushing candidates each need two related stats (e.g. a
# QB's yards AND touchdowns), sacks needs just the one.
LEADER_CATEGORY_STATS = {
    "passing": ["passing_yards", "passing_touchdowns"],
    "receiving": ["receiving_yards", "receiving_touchdowns"],
    "rushing": ["rushing_yards", "rushing_touchdowns"],
    "sacks": ["defensive_sacks"],
}

_CORS_HEADERS = {
    # Wildcard is safe here specifically because auth is a bearer JWT in
    # the Authorization header (validated by the Cognito authorizer
    # before this function ever runs), not a cookie -- there's no
    # cookie-based session for a permissive CORS origin to leak.
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Initialized once per container lifetime, reused across warm invocations
# -- same lazy-singleton pattern as normalize/handler.py's _get_storage().
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None


def _get_storage() -> FeatureStorage:
    global _storage
    if _storage is None:
        _storage = FeatureStorage()
    return _storage


def _get_model_bucket() -> S3Manager:
    global _model_bucket
    if _model_bucket is None:
        _model_bucket = S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION"))
    return _model_bucket


def _get_predictions_table() -> DynamoDBTable:
    global _predictions_table
    if _predictions_table is None:
        _predictions_table = DynamoDBTable(os.environ["PREDICTIONS_TABLE_NAME"], region=os.environ.get("AWS_REGION"))
    return _predictions_table


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def _model_name_to_prop(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def _record_prediction(event_key_value: str, model_key: str, value) -> None:
    _get_predictions_table().put_item({
        "event_key": event_key_value,
        "model_key": model_key,
        "predicted_value": value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def _get_cached_model(model_cache: dict, s3, model_name: str):
    """Several leader candidates within the same request often need the
    SAME model (e.g. every receiver candidate needs
    player-prop-receiving-yards) -- this loads each distinct model at
    most once per request instead of once per candidate."""
    if model_name not in model_cache:
        model_cache[model_name] = model_loader.load_current_model(s3, SPORT, model_name)
    return model_cache[model_name]


def _score_leader_candidate(storage, s3, model_cache: dict, feature_row: dict, stats: list[str]) -> dict:
    entity_id = feature_row["entity_id"]
    entity = storage.get_entity(SPORT, entity_id)
    result = {"entity_id": entity_id}
    if entity and entity.get("name"):
        result["name"] = entity["name"]

    for stat in stats:
        model_name = _model_name_to_prop(stat)
        try:
            booster, model_card = _get_cached_model(model_cache, s3, model_name)
        except model_loader.NoPromotedModelError:
            # This one stat's model hasn't been promoted yet -- leave it
            # out of the candidate's result rather than failing the whole
            # event prediction over a gap in an enhancement field.
            continue
        result[stat] = model_loader.predict(booster, model_card, feature_row)
    return result


def _predict_event_leaders(storage, s3, event_key_value: str) -> dict | None:
    """The `leaders` block -- passing/receiving/rushing/sacks leaders per
    team. Best-effort: any failure here is logged and swallowed rather
    than propagated, since the core win/margin/score predictions in
    _predict_event already succeeded by the time this runs and shouldn't
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
                storage, s3, model_cache, passing[0], LEADER_CATEGORY_STATS["passing"],
            ) if passing else None,
            "receiving": [
                _score_leader_candidate(storage, s3, model_cache, row, LEADER_CATEGORY_STATS["receiving"])
                for row in team_candidates["receiving"]
            ],
            "rushing": [
                _score_leader_candidate(storage, s3, model_cache, row, LEADER_CATEGORY_STATS["rushing"])
                for row in team_candidates["rushing"]
            ],
            "sacks": [
                _score_leader_candidate(storage, s3, model_cache, row, LEADER_CATEGORY_STATS["sacks"])
                for row in team_candidates["sacks"]
            ],
        }

    return {"home": team_leaders(candidates["home"]), "away": team_leaders(candidates["away"])}


def _predict_event(event_id: str) -> dict:
    storage = _get_storage()
    s3 = _get_model_bucket()
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_event_features(storage, SPORT, event_key_value)

    booster, model_card = model_loader.load_current_model(s3, SPORT, WIN_PROBABILITY_MODEL)
    home_win_probability = model_loader.predict(booster, model_card, feature_row)
    predictions = {
        "win_probability": {"home_win_probability": home_win_probability, "model_version": model_card["version"]},
    }
    _record_prediction(
        event_key_value, f"MODEL#{WIN_PROBABILITY_MODEL}#v{model_card['version']}", predictions["win_probability"],
    )

    for target, model_name in SCORE_MODELS.items():
        booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
        value = model_loader.predict(booster, model_card, feature_row)
        predictions[target] = {"value": value, "model_version": model_card["version"]}
        _record_prediction(event_key_value, f"MODEL#{model_name}#v{model_card['version']}", predictions[target])

    return {
        "event_key": event_key_value,
        "predictions": predictions,
        "leaders": _predict_event_leaders(storage, s3, event_key_value),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _predict_player_prop(event_id: str, entity_id: str, target_stat: str) -> dict:
    storage = _get_storage()
    s3 = _get_model_bucket()
    event_key_value = build_event_key(SPORT, event_id)
    feature_row = live_features.build_live_player_features(storage, SPORT, event_key_value, entity_id)

    model_name = _model_name_to_prop(target_stat)
    booster, model_card = model_loader.load_current_model(s3, SPORT, model_name)
    value = model_loader.predict(booster, model_card, feature_row)

    entity_key_value = build_entity_key(SPORT, entity_id)
    _record_prediction(
        event_key_value, f"MODEL#{model_name}#v{model_card['version']}#PLAYER#{entity_id}", {"value": value},
    )

    return {
        "event_key": event_key_value,
        "entity_key": entity_key_value,
        "stat": target_stat,
        "prediction": {"value": value, "model_version": model_card["version"]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _list_events(status: str) -> dict:
    storage = _get_storage()
    events = storage.get_all_events(SPORT, status=status)
    return {
        "sport": SPORT,
        "events": [
            {
                "event_id": e["event_id"],
                "event_date": e.get("event_date"),
                "status": e.get("status"),
                "season": e.get("season"),
                "season_type": e.get("season_type"),
                "week": e.get("week"),
                "participants": e.get("participants"),
            }
            for e in events
        ],
    }


def _list_models() -> dict:
    s3 = _get_model_bucket()
    prefix = f"{SPORT}/"
    model_names = sorted({key[len(prefix):].split("/")[0] for key in s3.list_keys(prefix)})

    models = []
    for model_name in model_names:
        pointer_key = current_version_key(SPORT, model_name)
        if not s3.object_exists(pointer_key):
            continue
        version = s3.get_json(pointer_key)["version"]
        card = s3.get_json(model_artifact_key(SPORT, model_name, version, "model_card.json"))
        top_features = [
            {"feature": name, "importance": value}
            for name, value in list(card.get("feature_importances", {}).items())[:5]
        ]
        models.append({
            "model_name": card["model_name"],
            "algorithm": card["algorithm"],
            "version": card["version"],
            "trained_at": card["trained_at"],
            **{k: v for k, v in card.items() if k in ("accuracy", "log_loss", "rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae")},
            "top_features": top_features,
        })
    return {"sport": SPORT, "models": models}


def _home_and_away(event: dict) -> tuple[str, str] | None:
    """Same lookup as live_features._home_away_ids, but returns None on a
    malformed event instead of raising -- season-wide aggregation walks
    hundreds of events and should skip one bad row, not abort the whole
    projection the way a single-event lookup correctly does."""
    participants = event.get("participants", [])
    home = next((p for p in participants if p.get("role") == "home"), None)
    away = next((p for p in participants if p.get("role") == "away"), None)
    if home is None or away is None:
        return None
    return home["entity_id"], away["entity_id"]


def _season_standings_inputs(storage: FeatureStorage) -> dict:
    """Fetches this season's completed+scheduled events once and derives
    everything season_simulation.simulate_season needs, plus each team's
    next scheduled event_key (reused by _leaderboards below)."""
    scheduled = storage.get_all_events(SPORT, status="scheduled")
    completed = storage.get_all_events(SPORT, status="completed")
    current_season = max(
        (e.get("season") for e in scheduled + completed if e.get("season") is not None), default=None,
    )
    scheduled = [e for e in scheduled if e.get("season") == current_season]
    completed = [e for e in completed if e.get("season") == current_season]

    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    point_differential: dict[str, int] = {}
    for event in completed:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        for entity_id, opponent_id in (home_away, home_away[::-1]):
            participant = next(p for p in event["participants"] if p.get("entity_id") == entity_id)
            opponent = next(p for p in event["participants"] if p.get("entity_id") == opponent_id)
            score = (participant.get("result") or {}).get("score")
            opponent_score = (opponent.get("result") or {}).get("score")
            if score is None or opponent_score is None:
                continue
            wins[entity_id] = wins.get(entity_id, 0) + (1 if score > opponent_score else 0)
            losses[entity_id] = losses.get(entity_id, 0) + (1 if score < opponent_score else 0)
            point_differential[entity_id] = point_differential.get(entity_id, 0) + (score - opponent_score)

    _, current_ratings = compute_elo_ratings(completed)

    scheduled_sorted = sorted(scheduled, key=lambda e: e.get("event_date", ""))
    remaining_games = []
    team_next_event: dict[str, str] = {}
    for event in scheduled_sorted:
        home_away = _home_and_away(event)
        if home_away is None:
            continue
        home_id, away_id = home_away
        remaining_games.append((home_id, away_id))
        team_next_event.setdefault(home_id, event["event_key"])
        team_next_event.setdefault(away_id, event["event_key"])

    return {
        "current_season": current_season,
        "completed_event_keys": {e["event_key"] for e in completed},
        "wins": wins,
        "losses": losses,
        "point_differential": point_differential,
        "current_ratings": current_ratings,
        "remaining_games": remaining_games,
        "team_next_event": team_next_event,
        "games_remaining": Counter(team_id for pair in remaining_games for team_id in pair),
    }


def _leaderboards(storage: FeatureStorage, s3, model_cache: dict, season_inputs: dict) -> dict:
    """Top-10 season-long leaderboard per tracked player-prop stat,
    projected as current season-to-date total + (their own model's
    prediction for their team's NEXT scheduled game * games remaining) --
    see season_simulation.project_leaderboard's own docstring for why
    this is a flat estimate rather than a per-opponent simulation."""
    season_player_stats = [
        row for row in storage.get_all_player_game_stats()
        if row.get("event_key") in season_inputs["completed_event_keys"]
    ]

    player_team: dict[str, str] = {}
    for row in season_player_stats:
        player_team.setdefault(row["entity_id"], row.get("team_id"))

    leaderboards: dict[str, list[dict]] = {}
    for stat in PLAYER_PROP_STATS:
        current_totals: dict[str, float] = {}
        for row in season_player_stats:
            value = row.get("stat_line", {}).get(stat)
            if value is not None:
                entity_id = row["entity_id"]
                current_totals[entity_id] = current_totals.get(entity_id, 0) + value

        model_name = _model_name_to_prop(stat)
        per_game_projections: dict[str, float] = {}
        for entity_id in current_totals:
            next_event_key = season_inputs["team_next_event"].get(player_team.get(entity_id))
            if next_event_key is None:
                continue
            try:
                feature_row = live_features.build_live_player_features(storage, SPORT, next_event_key, entity_id)
                booster, model_card = _get_cached_model(model_cache, s3, model_name)
                per_game_projections[entity_id] = model_loader.predict(booster, model_card, feature_row)
            except (live_features.EventNotFoundError, model_loader.NoPromotedModelError):
                continue
            except Exception:
                logger.exception("Failed to project %s for %s", stat, entity_id)
                continue

        games_remaining = {
            entity_id: season_inputs["games_remaining"].get(player_team.get(entity_id), 0)
            for entity_id in current_totals
        }

        top = season_simulation.project_leaderboard(current_totals, per_game_projections, games_remaining, top_n=10)
        for row in top:
            entity = storage.get_entity(SPORT, row["entity_id"])
            if entity and entity.get("name"):
                row["name"] = entity["name"]
        leaderboards[stat] = top

    return leaderboards


def _season_projection() -> dict:
    storage = _get_storage()
    s3 = _get_model_bucket()
    model_cache: dict = {}

    season_inputs = _season_standings_inputs(storage)
    simulation = season_simulation.simulate_season(
        season_inputs["wins"], season_inputs["losses"], season_inputs["point_differential"],
        season_inputs["remaining_games"], season_inputs["current_ratings"],
    )

    standings = sorted(
        (
            {
                "team_id": team_id,
                "wins": season_inputs["wins"].get(team_id, 0),
                "losses": season_inputs["losses"].get(team_id, 0),
                **projection,
            }
            for team_id, projection in simulation.items()
        ),
        key=lambda row: row["projected_wins"],
        reverse=True,
    )

    try:
        leaderboards = _leaderboards(storage, s3, model_cache, season_inputs)
    except Exception:
        logger.exception("Failed to build season leaderboards")
        leaderboards = None

    return {
        "sport": SPORT,
        "season": season_inputs["current_season"],
        "standings": standings,
        "leaderboards": leaderboards,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def lambda_handler(event, context):
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")

    try:
        if resource == "/nfl/events":
            return _response(200, _list_events(query_params.get("status", "scheduled")))

        if resource == "/nfl/models":
            return _response(200, _list_models())

        if resource == "/nfl/season":
            return _response(200, _season_projection())

        if resource == "/nfl/predictions/events/{event_id}/players/{entity_id}":
            target_stat = query_params.get("stat")
            if not target_stat:
                return _response(400, {"error": "Missing required query parameter: stat"})
            body = _predict_player_prop(path_params["event_id"], path_params["entity_id"], target_stat)
            return _response(200, body)

        if resource == "/nfl/predictions/events/{event_id}":
            body = _predict_event(path_params["event_id"])
            return _response(200, body)

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except live_features.EventNotFoundError as exc:
        return _response(404, {"error": str(exc)})
    except live_features.MalformedEventError as exc:
        return _response(422, {"error": str(exc)})
    except model_loader.NoPromotedModelError as exc:
        return _response(503, {"error": str(exc)})
    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
