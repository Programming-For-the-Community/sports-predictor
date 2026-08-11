"""
NFL inference Lambda -- a background compute worker, never invoked by
API Gateway. Two invocation shapes:

    {"detail-type": "ScheduledSeasonProjection"}
        -> weekly EventBridge Scheduler invoke; computes the season
           projection and writes it to S3.
    {"detail-type": "ComputeAndCachePrediction", "route": "event"|"player_prop", ...}
        -> fire-and-forget invoke from predict-read on a prediction-cache
           miss/stale-refresh; computes one prediction and writes it to
           the same S3 cache predict-read reads from.
"""
import logging
import os

import event_prediction
import season_projection
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nfl-predict")

# Lazy singletons, reused across warm invocations.
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
