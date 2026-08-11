"""
NCAAFB read-only serving Lambda. Triggered by API Gateway (REST API,
Lambda proxy integration) behind the same Cognito authorizer as the main
predict Lambda (Terraform/api-gateway.tf) -- every request reaching this
function has already had its JWT validated by API Gateway itself; this
code never checks auth.

Routes:
    GET /ncaafb/events?status=scheduled|completed
    GET /ncaafb/models
    GET /ncaafb/season
    GET /ncaafb/predictions/events/{event_id}
    GET /ncaafb/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards

See library.serving.ncaafb_reads (Source/library/serving/ncaafb_reads.py)
for the events/models/season request-shaping logic, shared with the main
predict Lambda -- that module's docstring covers the exact response
contract for those three.

The two prediction routes are NEW here (moved off predict/handler.py,
which used to serve them directly, computing live on every request) --
this is now a read-through cache in front of them, backed by S3
(library.storage.prediction_cache), with the actual computation always
happening asynchronously in the heavy predict Lambda:

    - Cache hit, fresh (model versions unchanged, and -- for a not-yet-
      played event -- still within the TTL): served straight from S3,
      200, identical response shape to what predict/handler.py used to
      return synchronously. No behavior change for this, the common,
      case.
    - Cache hit, STALE (a model got repromoted, or a scheduled event's
      cache aged past the TTL): still served immediately from S3 (the
      stale value is still a reasonable answer), 200, identical shape --
      but a background refresh is triggered first, so the NEXT request
      gets the fresher value. No response-shape change here either.
    - Cache MISS: nothing to serve yet. Triggers an async compute (a
      fire-and-forget invoke of the predict Lambda) and returns 202
      immediately: {"status": "computing", "retry_after_seconds": N}.
      THIS is the one real, frontend-visible contract change -- a cold
      request for an event/player that's never been requested before no
      longer blocks waiting for the ~20-50s computation (which used to
      risk a 504 against API Gateway's 29s ceiling for NCAAFB
      specifically, once its much larger backfilled history made
      live_features.py's per-request DynamoDB fetching expensive -- see
      that module's own docstring). The client is expected to poll again
      shortly; a repeat request for the same still-computing key returns
      202 again (not a duplicate compute -- prediction_cache.
      claim_in_progress de-dupes concurrent misses) until the first
      compute finishes and writes the cache.
    - A recognized-but-possibly-transient compute failure (event not
      ingested yet, no model promoted yet) surfaces as its own real
      status code (404/422/503) once the async compute has actually run
      and cached that outcome -- see prediction_cache.put_error_cached's
      own docstring. Until then, same as any other miss, it's a 202.

This Lambda exists ONLY to give these five routes a light cold start,
same reasoning as Source/aws-lambdas/nfl/predict-read/handler.py's own
docstring: none of them ever load or deserialize an ML model artifact,
so this Lambda's own requirements.txt needs nothing beyond boto3 (pre-
installed in the Lambda runtime) -- no xgboost, no scikit-learn, no
pandas. Zip-packaged (like ingest/normalize), not the container image
the predict Lambda needs for its own much larger dependency footprint --
see Terraform/lambda-ncaafb-predict-read.tf.
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-predict-read")

SPORT = "ncaafb"

# How long a client should wait before polling again on a 202 -- a plain
# UI hint, not enforced server-side (a request that polls sooner just
# sees the same cache state, possibly still 202).
RETRY_AFTER_SECONDS = 5

_CORS_HEADERS = {
    # Wildcard is safe here specifically because auth is a bearer JWT in
    # the Authorization header (validated by the Cognito authorizer
    # before this function ever runs), not a cookie -- there's no
    # cookie-based session for a permissive CORS origin to leak.
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Initialized once per container lifetime, reused across warm invocations
# -- same lazy-singleton pattern as predict/handler.py's _get_storage().
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
    """Fires the async compute unless someone else already appears to be
    computing this exact key right now -- see prediction_cache.
    claim_in_progress's own docstring for the de-dupe (and its accepted,
    narrow race)."""
    if prediction_cache.claim_in_progress(s3, cache_key):
        _get_predict_invoker().invoke_async(async_payload)


def _serve_or_trigger(s3, cache_key: str, current_model_versions, async_payload: dict) -> dict:
    """The read-through-with-async-populate core -- see this module's own
    docstring for the full state table (fresh/stale/miss/cached-error)."""
    entry = prediction_cache.get_cached(s3, cache_key)
    if entry is not None:
        if prediction_cache.is_error_entry(entry):
            if prediction_cache.is_error_entry_fresh(entry):
                status_code = prediction_cache.ERROR_STATUS_CODES.get(entry["error_type"], 500)
                return _response(status_code, {"error": entry["error"]})
            # Negative-cache entry expired -- fall through and retry as
            # if this were a plain miss (e.g. the event may have been
            # ingested since).
        else:
            if not prediction_cache.is_fresh(entry, current_model_versions):
                _trigger_refresh(s3, cache_key, async_payload)
            return _response(200, entry["result"])

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
