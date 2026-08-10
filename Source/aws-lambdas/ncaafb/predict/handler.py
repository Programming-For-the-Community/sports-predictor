"""
NCAAFB inference Lambda. Triggered by API Gateway (REST API, Lambda proxy
integration) behind the Cognito authorizer already wired in
Terraform/api-gateway.tf -- every request reaching this function has
already had its JWT validated by API Gateway itself; this code never
checks auth. Mirrors Source/aws-lambdas/nfl/predict/handler.py's overall
shape (see Terraform/lambda-ncaafb-predict.tf's own comment) -- NOT a
port of it; see live_features.py's own docstring for the leaders-block
differences and season_simulation.py's own docstring for how the
ScheduledSeasonProjection branch below differs from NFL's (no static
division table, CFP field selection needs the real trained
national-ranking model).

Routing and response-shaping only -- the actual prediction logic lives in
event_prediction.py and season_projection.py.

GET /ncaafb/events and GET /ncaafb/models are deliberately NOT served
here -- see Source/aws-lambdas/ncaafb/predict-read/handler.py. Same cold-
start-isolation reasoning as NFL's own predict/predict-read split (see
that Lambda's own docstring): neither route ever loads or deserializes an
ML model artifact, so they're served by a separate, much lighter Lambda.
GET /ncaafb/season is served from predict-read too, off the cache this
Lambda's own ScheduledSeasonProjection branch below writes to S3 -- see
season_projection.py's own docstring for why that route can't compute
this live per-request.

Routes (see Terraform/lambda-ncaafb-predict.tf for the API Gateway
wiring):
    GET /ncaafb/predictions/events/{event_id}
        -> win probability, margin, home score, and away score for one
           upcoming/completed matchup, plus a `leaders` block: one
           presumptive passing/receiving/rushing leader per team (no
           sacks -- see live_features.py's own docstring), `null` per
           category with no still-rostered leader found. `leaders` as a
           whole is `null` if it couldn't be computed at all --
           best-effort, never fails the core predictions above over it.
    GET /ncaafb/predictions/events/{event_id}/players/{entity_id}?stat=passing_yards
        -> one player-prop prediction for one player in one game. `stat`
           must be one of the trained TARGET_STAT values (see
           Terraform/dynamodb-sport-registry.tf's ncaafb_player_prop_stats).

{event_id}/{entity_id} are raw CFBD ids (e.g. "401520281", "4426348"),
not the internal SPORT#NCAAFB#EVENT#... key -- translated via
library.schema.keys the same way every other NCAAFB adapter does.

Both API-Gateway-triggered routes above compute live on every request (no
caching) and log to the predictions table for the audit trail -- see
design/DATA_SCHEMA.md's Predictions table. The predictions table's IAM
grant is PutItem-only (see Terraform/iam-lambda-inference.tf); this
function never reads a past prediction back.
"""
import json
import logging
import os

from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.storage.feature_storage import FeatureStorage
import event_prediction
import live_features
import model_loader
import season_projection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-predict")

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


def lambda_handler(event, context):
    if event.get("detail-type") == "ScheduledSeasonProjection":
        return season_projection.run_scheduled(_get_storage(), _get_model_bucket())

    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}
    resource = event.get("resource", "")

    try:
        if resource == "/ncaafb/predictions/events/{event_id}/players/{entity_id}":
            target_stat = query_params.get("stat")
            if not target_stat:
                return _response(400, {"error": "Missing required query parameter: stat"})
            body = event_prediction.predict_player_prop(
                _get_storage(), _get_model_bucket(), _get_predictions_table(),
                path_params["event_id"], path_params["entity_id"], target_stat,
            )
            return _response(200, body)

        if resource == "/ncaafb/predictions/events/{event_id}":
            body = event_prediction.predict_event(
                _get_storage(), _get_model_bucket(), _get_predictions_table(), path_params["event_id"],
            )
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
