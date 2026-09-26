"""
Prediction-scheduler Lambda. Invoked every 5 minutes by EventBridge
Scheduler (Terraform/scheduler-prediction-scheduler.tf). Shared across the
head-to-head sports; all decision logic lives in
library.serving.prediction_scheduler -- this file only wires AWS clients.

For each upcoming event it (a) re-runs the sport's ingest shortly before
kickoff so injuries/rosters/depth charts are current, then (b) triggers the
sport's predict Lambda to write the event's immutable pre-kickoff snapshot.
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from library.aws import lambda_singletons
from library.aws.boto_config import DEFAULT_CONFIG
from library.aws.dynamodb_table import DynamoDBTable
from library.serving import prediction_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("prediction-scheduler")

# Created lazily: a boto3 Lambda client needs a region, which an import-time
# call would demand of every environment that merely imports this module (CI).
_lambda_client = None
_events_table: DynamoDBTable | None = None
_predictions_table: DynamoDBTable | None = None


def _get_lambda_client():
    return lambda_singletons.get_or_create(
        globals(), "_lambda_client", lambda: boto3.client("lambda", region_name=os.environ.get("AWS_REGION"), config=DEFAULT_CONFIG),
    )


def _get_events_table() -> DynamoDBTable:
    return lambda_singletons.get_or_create(
        globals(), "_events_table", lambda: DynamoDBTable(os.environ["EVENTS_TABLE_NAME"], region=os.environ.get("AWS_REGION")),
    )


def _get_predictions_table() -> DynamoDBTable:
    return lambda_singletons.get_or_create(
        globals(), "_predictions_table", lambda: DynamoDBTable(os.environ["PREDICTIONS_TABLE_NAME"], region=os.environ.get("AWS_REGION")),
    )


def _invoke_async(function_name: str, payload: dict) -> None:
    logger.info("Invoking %s with %s", function_name, json.dumps(payload))
    _get_lambda_client().invoke(FunctionName=function_name, InvocationType="Event", Payload=json.dumps(payload).encode("utf-8"))


def lambda_handler(event, context):
    now = datetime.now(timezone.utc)
    events_table = _get_events_table()
    summary = prediction_scheduler.run_tick(
        now,
        prediction_scheduler.SPORT_SCHEDULES,
        lambda sport: prediction_scheduler.upcoming_events(events_table, sport, now),
        _get_predictions_table(),
        _invoke_async,
        os.environ["PROJECT_NAME"],
    )
    logger.info("Tick summary: %s", json.dumps(summary))
    return summary
