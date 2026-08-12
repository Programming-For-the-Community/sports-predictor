"""
NCAAFB read-only serving Lambda. Triggered by API Gateway behind the
Cognito authorizer.

Routes:
    GET /ncaafb/events?status=scheduled|completed
    GET /ncaafb/models
    GET /ncaafb/season
    GET /ncaafb/predictions/events/{event_id}
    GET /ncaafb/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards

See library.serving.ncaafb_reads for events/models/season. The two
prediction routes are a read-through cache (library.storage.
prediction_cache) in front of the predict Lambda, which does the actual
computation asynchronously:

    - Fresh cache hit: 200, served from S3, body carries "stale": false.
    - Stale cache hit: 203, served from S3, body carries "stale": true and
      "retry_after_seconds" alongside the (possibly outdated) prediction
      fields, background refresh triggered. A repeat request while that
      refresh is still in flight returns 203 again without triggering a
      second one.
    - Cache miss: 202 {"status": "computing", "retry_after_seconds": N},
      async compute triggered. A repeat request for the same key returns
      202 again without triggering a second compute.
    - Cached negative result (event not ingested, no model promoted):
      its own mapped status code (404/422/503).

No ML dependencies -- zip-packaged, not the predict Lambda's container image.
"""
import json
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.lambda_invoker import LambdaInvoker
from library.aws.s3_manager import S3Manager
from library.schema.keys import event_key as build_event_key
from library.serving.ncaafb_reads import get_season_projection, list_events, list_models
from library.storage import prediction_cache
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaafb-predict-read")

SPORT = "ncaafb"

RETRY_AFTER_SECONDS = 5  # UI hint only, not enforced server-side

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Lazy singletons, reused across warm invocations.
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None
_predict_invoker: LambdaInvoker | None = None


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


def _get_predict_invoker() -> LambdaInvoker:
    global _predict_invoker
    if _predict_invoker is None:
        _predict_invoker = LambdaInvoker(os.environ["PREDICT_FUNCTION_NAME"], region=os.environ.get("AWS_REGION"))
    return _predict_invoker


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def _trigger_refresh(s3, cache_key: str, async_payload: dict) -> None:
    if prediction_cache.claim_in_progress(s3, cache_key):
        _get_predict_invoker().invoke_async(async_payload)


def _serve_or_trigger(s3, cache_key: str, current_model_versions, async_payload: dict) -> dict:
    entry = prediction_cache.get_cached(s3, cache_key)
    if entry is not None:
        if prediction_cache.is_error_entry(entry):
            if prediction_cache.is_error_entry_fresh(entry):
                status_code = prediction_cache.ERROR_STATUS_CODES.get(entry["error_type"], 500)
                return _response(status_code, {"error": entry["error"]})
            # expired -- fall through, retry as a miss
        else:
            if not prediction_cache.is_fresh(entry, current_model_versions):
                # 203, not 200 -- lets the frontend show a "refreshing"
                # indicator and silently re-poll instead of treating this
                # the same as a genuinely current result (see
                # EventPrediction.stale on the frontend).
                _trigger_refresh(s3, cache_key, async_payload)
                return _response(203, {**entry["result"], "stale": True, "retry_after_seconds": RETRY_AFTER_SECONDS})
            return _response(200, {**entry["result"], "stale": False})

    _trigger_refresh(s3, cache_key, async_payload)
    return _response(202, {"status": "computing", "retry_after_seconds": RETRY_AFTER_SECONDS})


def lambda_handler(event, context):
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")

    try:
        if resource == "/ncaafb/events":
            status = query_params.get("status", "scheduled")
            body = list_events(_get_storage(), _get_predictions_table(), SPORT, status)
            return _response(200, body)

        if resource == "/ncaafb/models":
            body = list_models(_get_model_bucket(), SPORT)
            return _response(200, body)

        if resource == "/ncaafb/season":
            body = get_season_projection(_get_model_bucket(), SPORT)
            if body is None:
                return _response(503, {"error": "Season projection not yet available -- check back after the next scheduled update"})
            return _response(200, body)

        if resource == "/ncaafb/predictions/events/{event_id}":
            event_id = path_params["event_id"]
            event_key_value = build_event_key(SPORT, event_id)
            s3 = _get_model_bucket()
            cache_key = prediction_cache.event_prediction_cache_key(SPORT, event_key_value)
            current_versions = prediction_cache.current_core_model_versions(s3, SPORT)
            return _serve_or_trigger(
                s3, cache_key, current_versions,
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": event_id},
            )

        if resource == "/ncaafb/predictions/events/{event_id}/players/{entity_id}":
            target_stat = query_params.get("stat")
            if not target_stat:
                return _response(400, {"error": "Missing required query parameter: stat"})
            event_id = path_params["event_id"]
            entity_id = path_params["entity_id"]
            event_key_value = build_event_key(SPORT, event_id)
            s3 = _get_model_bucket()
            cache_key = prediction_cache.player_prop_cache_key(SPORT, event_key_value, entity_id, target_stat)
            current_version = prediction_cache.current_player_prop_model_version(s3, SPORT, target_stat)
            return _serve_or_trigger(
                s3, cache_key, current_version,
                {
                    "detail-type": "ComputeAndCachePrediction", "route": "player_prop",
                    "event_id": event_id, "entity_id": entity_id, "stat": target_stat,
                },
            )

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
