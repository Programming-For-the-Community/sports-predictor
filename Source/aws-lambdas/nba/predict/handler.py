"""
NBA inference Lambda -- a background compute worker, never invoked by
API Gateway directly. Two invocation shapes:

    {"detail-type": "ScheduledSeasonProjection"}
        -> weekly EventBridge Scheduler invoke; computes the season
           projection (standings + play-in/playoff odds + NBA Cup +
           player-prop leaderboards) and writes it to S3.
    {"detail-type": "ComputeAndCachePrediction", "route": "event"|"player_prop", ...}
        -> fire-and-forget invoke from predict-read on a prediction-cache
           miss/stale-refresh; computes one prediction and writes it to
           the same S3 cache predict-read reads from.
"""
import logging
import os

import event_prediction
import season_projection
from library.aws import lambda_singletons
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.logging_safety import safe_log_value
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nba-predict")

# Lazy singletons, reused across warm invocations.
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None


def _get_storage() -> FeatureStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", FeatureStorage)


def _get_model_bucket() -> S3Manager:
    return lambda_singletons.get_or_create(
        globals(), "_model_bucket",
        lambda: S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION")),
    )


def _get_predictions_table() -> DynamoDBTable:
    return lambda_singletons.get_or_create(
        globals(), "_predictions_table",
        lambda: DynamoDBTable(os.environ["PREDICTIONS_TABLE_NAME"], region=os.environ.get("AWS_REGION")),
    )


def lambda_handler(event, context):
    # EventBridge Scheduler warmup ping (scheduler-predict-warmup.tf) --
    # keeps a container past its own (slow, xgboost/pandas/sklearn-heavy)
    # cold-start import chain so a real request lands on an
    # already-initialized environment instead of paying that cost itself.
    if event.get("warmup"):
        return lambda_singletons.warm(_get_storage, _get_model_bucket, _get_predictions_table)

    if event.get("detail-type") == "ScheduledSeasonProjection":
        return season_projection.run_scheduled(_get_storage(), _get_model_bucket(), _get_predictions_table())

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

    logger.error("Unrecognized invocation shape (no known detail-type): %s", safe_log_value(event))
    return {"status": "error", "message": "Unrecognized invocation"}
