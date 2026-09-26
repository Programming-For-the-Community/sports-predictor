"""
Model-performance Lambda. Invoked daily by EventBridge Scheduler
(Terraform/scheduler-model-performance.tf), after the daily ingest has landed
the previous day's results. Shared across every sport; the work
itself lives in library.performance.runner -- this file only wires AWS
clients.

For each sport it scores every currently-promoted model against the
season's completed events (using each event's pre-kickoff snapshot) and
writes the scorecard the Performance tab reads
(GET /{sport}/model-performance).

An optional {"sports": ["nfl"]} payload runs just those sports.
"""
import json
import logging
import os
from datetime import date

from library.aws import lambda_singletons
from library.aws.dynamodb_table import DynamoDBTable
from library.aws.s3_manager import S3Manager
from library.performance import runner
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("model-performance")

SPORTS = ("nfl", "ncaafb", "nba", "ncaambb", "pga", "f1")

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
    sports = (event or {}).get("sports") or SPORTS
    summary: dict[str, dict] = {}
    for sport in sports:
        try:
            document = runner.run_sport(sport, _get_storage(), _get_predictions_table(), _get_model_bucket(), date.today())
        except Exception:
            # One sport failing must not stop the others' scorecards.
            logger.exception("Failed building the %s scorecard", sport)
            summary[sport] = {"status": "error"}
            continue
        summary[sport] = {"status": "ok", "models": len(document["models"]), **document["coverage"]}
    logger.info("Scorecards: %s", json.dumps(summary))
    return summary
