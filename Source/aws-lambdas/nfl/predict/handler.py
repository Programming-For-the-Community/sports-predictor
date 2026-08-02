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
           feature vector (see live_features.build_live_event_features).
    GET /nfl/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards
        -> one player-prop prediction for one player in one game. `stat`
           must be one of the trained TARGET_STAT values (see
           Terraform/scheduler-nfl-train-player-prop-model.tf's
           nfl_player_prop_stats).

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
import json
import logging
import os
from datetime import datetime, timezone

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.schema.keys import entity_key as build_entity_key
from library.schema.keys import event_key as build_event_key
from library.storage.feature_storage import FeatureStorage
from library.storage.model_artifacts import current_version_key, model_artifact_key
import live_features
import model_loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-predict")

SPORT = "nfl"
WIN_PROBABILITY_MODEL = "win-probability"
SCORE_MODELS = {"margin": "score-margin", "home_score": "home-score", "away_score": "away-score"}

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


def lambda_handler(event, context):
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")

    try:
        if resource == "/nfl/events":
            return _response(200, _list_events(query_params.get("status", "scheduled")))

        if resource == "/nfl/models":
            return _response(200, _list_models())

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
