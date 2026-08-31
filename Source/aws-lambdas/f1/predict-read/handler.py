"""
F1 read-only serving Lambda. Triggered by API Gateway behind the Cognito
authorizer.

Routes:
    GET /f1/events?status=scheduled|completed
    GET /f1/models
    GET /f1/season
    GET /f1/predictions/events/{event_id}

GET /f1/season is a pure read-through of the championship season
simulation the predict Lambda's own weekly EventBridge trigger writes
(aws-lambdas/f1/predict/season_projection.py) -- never computed live here,
same 503-when-not-yet-available contract every other sport's own season
route uses. No per-driver prediction sub-route (unlike NBA's
.../players/{entity_id}) -- one race compute already scores every driver
(and every constructor, for a "field" event), there's nothing narrower to
fetch.

The one prediction route is a read-through cache (library.storage.
prediction_cache) in front of the predict Lambda, which does the actual
computation asynchronously -- same 200/203/202/mapped-error-code contract
as every other sport's own predict-read (see PGA's own predict-read/
handler.py docstring for the full freshness/in-progress-claim protocol
this mirrors exactly).

No ML dependencies -- zip-packaged, not the predict Lambda's container
image.
"""
import json
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.lambda_invoker import LambdaInvoker
from library.aws.s3_manager import S3Manager
from library.schema.keys import event_key as build_event_key
from library.serving import f1_reads
from library.serving.common import list_models
from library.serving.viewer_analytics import log_viewer_analytics
from library.storage import prediction_cache
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("f1-predict-read")

SPORT = "f1"

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


def _freshness_inputs_for_event(s3, storage, event_id: str) -> tuple[dict, int | None]:
    """(current_model_versions, extra_fingerprint) -- the right model-name
    map depends on the event's own event_type (field/sprint, see
    library.serving.f1_reads.model_versions_for). current_model_versions
    falls back to {} (never matches a real cached entry's own model_
    versions, so is_fresh always reports stale rather than silently
    serving a wrong-shape comparison) if the event doesn't exist yet or
    has an event_type this Lambda doesn't recognize -- never raises on a
    read path."""
    event = storage.get_event(build_event_key(SPORT, event_id))
    if event is None:
        return {}, None
    try:
        models = f1_reads.model_versions_for(event.get("event_type"))
    except KeyError:
        return {}, None
    return prediction_cache.current_model_versions(s3, SPORT, models), f1_reads.result_fingerprint(event)


def _serve_or_trigger(s3, cache_key: str, current_model_versions, extra_fingerprint, async_payload: dict) -> dict:
    entry = prediction_cache.get_cached(s3, cache_key)
    if entry is not None:
        if prediction_cache.is_error_entry(entry):
            if prediction_cache.is_error_entry_fresh(entry):
                status_code = prediction_cache.ERROR_STATUS_CODES.get(entry["error_type"], 500)
                return _response(status_code, {"error": entry["error"]})
            # expired -- fall through, retry as a miss
        else:
            if not prediction_cache.is_fresh(entry, current_model_versions, extra_fingerprint):
                # 203, not 200 -- lets the frontend show a "refreshing"
                # indicator and silently re-poll instead of treating this
                # the same as a genuinely current result.
                _trigger_refresh(s3, cache_key, async_payload)
                return _response(203, {**entry["result"], "stale": True, "retry_after_seconds": RETRY_AFTER_SECONDS})
            return _response(200, {**entry["result"], "stale": False})

    _trigger_refresh(s3, cache_key, async_payload)
    return _response(202, {"status": "computing", "retry_after_seconds": RETRY_AFTER_SECONDS})


def lambda_handler(event, context):
    # EventBridge Scheduler warmup ping (scheduler-predict-read-
    # warmup.tf) -- no "resource" key, so this can't collide with a real
    # API Gateway route.
    if event.get("warmup"):
        _get_storage()
        _get_model_bucket()
        _get_predictions_table()
        return _response(200, {"status": "warm"})

    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")
    log_viewer_analytics(logger, SPORT, resource, event.get("httpMethod"), event.get("headers"))

    try:
        if resource == "/f1/events":
            status = query_params.get("status", "scheduled")
            body = f1_reads.list_events(_get_storage(), SPORT, status)
            return _response(200, body)

        if resource == "/f1/models":
            body = list_models(_get_model_bucket(), SPORT)
            return _response(200, body)

        if resource == "/f1/season":
            body = f1_reads.get_season_projection(_get_model_bucket(), SPORT)
            if body is None:
                return _response(503, {"error": "Season projection not yet available -- check back after the next scheduled update"})
            return _response(200, body)

        if resource == "/f1/predictions/events/{event_id}":
            event_id = path_params["event_id"]
            event_key_value = build_event_key(SPORT, event_id)
            s3 = _get_model_bucket()
            storage = _get_storage()
            cache_key = prediction_cache.event_prediction_cache_key(SPORT, event_key_value)
            current_versions, extra_fingerprint = _freshness_inputs_for_event(s3, storage, event_id)
            return _serve_or_trigger(
                s3, cache_key, current_versions, extra_fingerprint,
                {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": event_id},
            )

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
