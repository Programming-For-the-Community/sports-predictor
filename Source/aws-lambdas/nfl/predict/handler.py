"""
NFL inference Lambda -- a pure background compute worker, never invoked
by API Gateway directly (see Terraform/api-gateway-nfl-predict.tf: every
GET route, including the two prediction routes, is served by predict-
read/handler.py instead). Two invocation shapes, both async (EventBridge
Scheduler or a fire-and-forget Lambda invoke), neither bound by API
Gateway's 29s integration ceiling -- only this Lambda's own 300s timeout
applies:

    {"detail-type": "ScheduledSeasonProjection"}
        -> Terraform/scheduler-nfl-season-projection.tf's weekly direct
           invoke. season_projection._leaderboards alone takes 26-29s,
           a duration no per-request optimization can reliably stay
           under -- this Lambda computes the season projection once and
           writes it to S3, and GET /nfl/season is served from that
           cached object by predict-read (library.serving.nfl_reads.
           get_season_projection).
    {"detail-type": "ComputeAndCachePrediction", "route": "event"|"player_prop", ...}
        -> predict-read/handler.py's own fire-and-forget invoke on a
           prediction-cache miss/stale-refresh (library.storage.
           prediction_cache) -- computes one event or player-prop
           prediction (event_prediction.predict_event/predict_player_
           prop, the same live_features.build_live_event_features/
           build_live_event_leader_candidates logic this Lambda has
           always used) and writes it to the same S3 cache predict-read
           reads from. See event_prediction.compute_and_cache_event/
           compute_and_cache_player_prop's own docstrings, including how
           a recognized-but-possibly-transient failure (event not
           ingested yet, no model promoted yet) becomes a short-lived
           negative cache entry instead of leaving a request stuck at
           "computing" forever.

This split (predict-read owns every HTTP response, predict only ever
computes in the background) also means neither prediction route can ever
504 against API Gateway's ceiling anymore, regardless of how long a cold
(uncached) computation takes.
"""
import logging
import os

import event_prediction
import season_projection
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-predict")

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


def lambda_handler(event, context):
    if event.get("detail-type") == "ScheduledSeasonProjection":
        return season_projection.run_scheduled(_get_storage(), _get_model_bucket())

    if event.get("detail-type") == "ComputeAndCachePrediction":
        if event["route"] == "event":
            event_prediction.compute_and_cache_event(
                _get_storage(), _get_model_bucket(), _get_predictions_table(), event["event_id"],
            )
        elif event["route"] == "player_prop":
            event_prediction.compute_and_cache_player_prop(
                _get_storage(), _get_model_bucket(), _get_predictions_table(),
                event["event_id"], event["entity_id"], event["stat"],
            )
        return {"status": "ok"}

    logger.error("Unrecognized invocation shape (no known detail-type): %r", event)
    return {"status": "error", "message": "Unrecognized invocation"}
