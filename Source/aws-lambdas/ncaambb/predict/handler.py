"""
NCAA MBB inference Lambda -- a background compute worker, never invoked by
API Gateway directly. Two invocation shapes:

    {"detail-type": "ComputeAndCachePrediction", "route": "event"|"player_prop", ...}
        -> fire-and-forget invoke from predict-read on a prediction-cache
           miss/stale-refresh; computes one prediction and writes it to
           the same S3 cache predict-read reads from.

    {"detail-type": "ScheduledSeasonProjection"}
        -> Terraform/scheduler-ncaambb-season-projection.tf's own direct
           EventBridge invoke; recomputes standings + both postseason
           brackets and writes them to S3. Same shape as nba/predict/
           handler.py's own branch.
"""
import logging
import os

import event_prediction
import season_projection
from library.aws import lambda_singletons
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.aws.serving_resources import ServingResources
from library.serving.predict_lambda_handler import make_event_prediction_lambda_handler
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaambb-predict")

# Lazy singletons, reused across warm invocations.
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None
_raw_bucket: S3Manager | None = None

_resources = ServingResources(globals())
_get_storage = _resources.storage
_get_model_bucket = _resources.model_bucket
_get_predictions_table = _resources.predictions_table


def _get_raw_bucket() -> S3Manager:
    # Read-only in practice -- season_projection.py only ever calls
    # get_json/object_exists on this, scoped by IAM to the ncaambb/
    # conference-membership/* prefix schedule-sync's own handler.py
    # writes (see that module's own CONFERENCE MEMBERSHIP docstring
    # section for why this Lambda can't resolve it itself).
    return lambda_singletons.get_or_create(
        globals(), "_raw_bucket",
        lambda: S3Manager(os.environ["RAW_BUCKET_NAME"], region=os.environ.get("AWS_REGION")),
    )


# EventBridge Scheduler warmup ping (scheduler-predict-warmup.tf) -- keeps
# a container past its own (slow, xgboost/pandas/sklearn-heavy) cold-start
# import chain so a real request lands on an already-initialized
# environment instead of paying that cost itself.
lambda_handler = make_event_prediction_lambda_handler(
    resources=_resources, event_prediction=event_prediction,
    run_scheduled_fn=lambda: season_projection.run_scheduled(*_resources.all(), _get_raw_bucket()),
    logger=logger, player_props=True,
    extra_warmups=(_get_raw_bucket,),
)
