"""
NFL read-only serving Lambda. Triggered by API Gateway (REST API, Lambda
proxy integration) behind the same Cognito authorizer as the main predict
Lambda (Terraform/api-gateway.tf) -- every request reaching this function
has already had its JWT validated by API Gateway itself; this code never
checks auth.

Routes:
    GET /nfl/events?status=scheduled|completed
    GET /nfl/models
    GET /nfl/season

See library.serving.nfl_reads (Source/library/serving/nfl_reads.py) for
the actual request-shaping logic, shared with the main predict Lambda
(Source/aws-lambdas/nfl/predict/handler.py) -- that module's docstring
covers the exact response contract for all three routes.

GET /nfl/season reads a cached S3 object (get_season_projection) rather
than computing anything -- the projection itself (standings + per-stat
leaderboards) is computed once a week by the predict Lambda's own
ScheduledSeasonProjection branch, not per-request; see that module's
docstring for why (26-29s of computation cannot be served synchronously
through API Gateway's 29s integration ceiling). Serving the cached read
from here instead of the predict Lambda is the same cold-start reasoning
as events/models below: this route never loads or deserializes an ML
model artifact either.

This Lambda exists ONLY to give these two routes a light cold start.
Neither ever loads or deserializes an ML model artifact (list_models only
reads a model card's JSON metadata; list_events only reads events +
already-logged predictions from DynamoDB), so this Lambda's own
requirements.txt needs nothing beyond boto3 (pre-installed in the Lambda
runtime) -- no xgboost, no scikit-learn, no pandas, none of the
predict Lambda's real, confirmed-live-to-occasionally-exceed-10-seconds
import cost. Zip-packaged (like ingest/normalize), not the container
image the predict Lambda needs for its own much larger dependency
footprint -- see Terraform/lambda-nfl-predict-read.tf.
"""
import json
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.serving.nfl_reads import get_season_projection, list_events, list_models
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-predict-read")

SPORT = "nfl"

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


def lambda_handler(event, context):
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")

    try:
        if resource == "/nfl/events":
            status = query_params.get("status", "scheduled")
            body = list_events(_get_storage(), _get_predictions_table(), SPORT, status)
            return _response(200, body)

        if resource == "/nfl/models":
            body = list_models(_get_model_bucket(), SPORT)
            return _response(200, body)

        if resource == "/nfl/season":
            body = get_season_projection(_get_model_bucket(), SPORT)
            if body is None:
                return _response(503, {"error": "Season projection not yet available -- check back after the next scheduled update"})
            return _response(200, body)

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
