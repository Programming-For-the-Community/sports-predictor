"""
PGA inference Lambda -- a background compute worker, never invoked by
API Gateway directly. Two invocation shapes:

    {"detail-type": "ScheduledSeasonProjection"}
        -> weekly EventBridge Scheduler invoke; computes the FedEx Cup
           season simulation (standings + Playoffs-field/Champion
           probabilities -- season_projection.py) and writes it to S3.
    {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": ...}
        -> fire-and-forget invoke from predict-read on a prediction-cache
           miss/stale-refresh; computes one event's prediction (field/
           match_play/cup, dispatched inside event_prediction.predict_event)
           and writes it to the same S3 cache predict-read reads from.

No "player_prop" route (unlike NBA/NFL/NCAAFB/NCAAMBB) -- PGA has no
per-player-prop model, every golfer-level model already needs the whole
field to produce a ranked response, so there's nothing narrower to
compute on demand.
"""
import logging
import os

import event_prediction
import season_projection
from library.aws import lambda_singletons
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.serving.predict_lambda_handler import make_lambda_handler
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("pga-predict")

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


# EventBridge Scheduler warmup ping (scheduler-predict-warmup.tf) -- keeps
# a container past its own (slow, xgboost/pandas/sklearn-heavy) cold-start
# import chain so a real request lands on an already-initialized
# environment instead of paying that cost itself.
lambda_handler = make_lambda_handler(
    warmup_fn=lambda: lambda_singletons.warm(_get_storage, _get_model_bucket, _get_predictions_table),
    run_scheduled_fn=lambda: season_projection.run_scheduled(_get_storage(), _get_model_bucket()),
    compute_and_cache_event_fn=lambda event_id: event_prediction.compute_and_cache_event(
        _get_storage(), _get_model_bucket(), _get_predictions_table(), event_id,
    ),
    snapshot_event_fn=lambda event_id: event_prediction.snapshot_event(
        _get_storage(), _get_model_bucket(), _get_predictions_table(), event_id,
    ),
    logger=logger,
)
