"""
F1 inference Lambda -- a background compute worker, never invoked by
API Gateway directly. Two invocation shapes:

    {"detail-type": "ScheduledSeasonProjection"}
        -> weekly EventBridge Scheduler invoke; computes the championship
           season simulation (driver + constructor standings/probabilities
           -- season_projection.py) and writes it to S3.
    {"detail-type": "ComputeAndCachePrediction", "route": "event", "event_id": ...}
        -> fire-and-forget invoke from predict-read on a prediction-cache
           miss/stale-refresh; computes one event's prediction (field/
           sprint, dispatched inside event_prediction.predict_event) and
           writes it to the same S3 cache predict-read reads from.

No "player_prop" route (unlike NFL/NBA/NCAAFB/NCAAMBB) -- same reasoning
PGA's own handler.py docstring gives: F1's models already need each
race's whole field/constructor set to produce a ranked response, so
there's nothing narrower to compute on demand.
"""
import logging

import event_prediction
import season_projection
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.aws.serving_resources import ServingResources
from library.serving.predict_lambda_handler import make_event_prediction_lambda_handler
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("f1-predict")

# Lazy singletons, reused across warm invocations.
_storage: FeatureStorage | None = None
_model_bucket: S3Manager | None = None
_predictions_table: DynamoDBTable | None = None

_resources = ServingResources(globals())
_get_storage = _resources.storage
_get_model_bucket = _resources.model_bucket
_get_predictions_table = _resources.predictions_table


# EventBridge Scheduler warmup ping (scheduler-predict-warmup.tf) -- keeps
# a container past its own (slow, xgboost/pandas/sklearn-heavy) cold-start
# import chain so a real request lands on an already-initialized
# environment instead of paying that cost itself.
lambda_handler = make_event_prediction_lambda_handler(
    resources=_resources, event_prediction=event_prediction,
    run_scheduled_fn=lambda: season_projection.run_scheduled(_resources.storage(), _resources.model_bucket()),
    logger=logger, player_props=False,
)
