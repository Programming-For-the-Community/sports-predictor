"""
NFL inference Lambda. Triggered by API Gateway (REST API, Lambda proxy
integration) behind the Cognito authorizer already wired in
Terraform/api-gateway.tf -- every request reaching this function has
already had its JWT validated by API Gateway itself; this code never
checks auth.

Routing and response-shaping only -- the actual prediction logic lives in
event_prediction.py (the two on-demand routes below) and
season_projection.py (the scheduled season-wide computation).

GET /nfl/events and GET /nfl/models are deliberately NOT served here --
see Source/aws-lambdas/nfl/predict-read/handler.py. Neither route ever
loads or deserializes an ML model artifact (library.serving.nfl_reads.
list_models only reads a model card's JSON metadata; list_events only
reads events + already-logged predictions from DynamoDB), so they're
served by a separate, much lighter Lambda that never imports xgboost/
scikit-learn/pandas at all -- this Lambda's own cold start (real, heavy
dependency chain that can exceed Lambda's non-configurable 10-second
init-phase ceiling) shouldn't be paid by routes that never need it,
especially since those two are also this API's most frequently hit
traffic.

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

{event_id}/{entity_id} are raw ESPN ids (e.g. "401547417",
"3139477"), not the internal SPORT#NFL#EVENT#... key -- translated via
library.schema.keys the same way every other NFL adapter does.

Both routes above compute live on every request (no caching, no serving
a previously-computed value back) and log to the predictions table for
the audit trail -- see design/DATA_SCHEMA.md's Predictions table. The
predictions table's IAM grant is PutItem-only (see
Terraform/iam-lambda-inference.tf); this function never reads a past
prediction back.

GET /nfl/season is NOT served here either, despite this being the only
Lambda that ever computes it -- see Terraform/lambda-nfl-predict-read.tf.
season_projection._leaderboards alone takes 26-29s against API Gateway's
hard, non-configurable 29s integration ceiling, a duration no per-request
optimization can reliably stay under. Instead, this Lambda is invoked
directly by Terraform/scheduler-nfl-season-projection.tf's weekly
EventBridge Scheduler target (see the ScheduledSeasonProjection branch in
lambda_handler below), which computes once and writes the result to S3;
GET /nfl/season itself is served from that cached object by the light
predict-read Lambda (library.serving.nfl_reads.get_season_projection).
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
logger = logging.getLogger("nfl-predict")

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
        if resource == "/nfl/predictions/events/{event_id}/players/{entity_id}":
            target_stat = query_params.get("stat")
            if not target_stat:
                return _response(400, {"error": "Missing required query parameter: stat"})
            body = event_prediction.predict_player_prop(
                _get_storage(), _get_model_bucket(), _get_predictions_table(),
                path_params["event_id"], path_params["entity_id"], target_stat,
            )
            return _response(200, body)

        if resource == "/nfl/predictions/events/{event_id}":
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
