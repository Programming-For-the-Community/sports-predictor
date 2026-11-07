"""
Assistant cost-snapshot Lambda. Invoked daily by EventBridge Scheduler
(Terraform/scheduler-assistant-producers.tf); writes the project-only cost
snapshot the admin assistant's get_cost_summary tool reads -- the work
itself lives in library.assistant.cost_snapshot.

{"backfill": true} re-pulls the last COST_BACKFILL_MONTHS months; the first run (no meta
document yet) does so on its own.
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from library.assistant import cost_snapshot
from library.assistant.settings import ProducerSettings
from library.aws.boto_config import DEFAULT_CONFIG
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("assistant-cost-snapshot")

# Cost Explorer and Budgets are global services, served from the region
# Terraform passes (us-east-1).
_ce = boto3.client("ce", region_name=os.environ["COST_EXPLORER_REGION"], config=DEFAULT_CONFIG)
_budgets = boto3.client("budgets", region_name=os.environ["COST_EXPLORER_REGION"], config=DEFAULT_CONFIG)
_s3: S3Manager | None = None


def _get_s3() -> S3Manager:
    global _s3
    if _s3 is None:
        _s3 = S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION"))
    return _s3


def lambda_handler(event, context):
    summary = cost_snapshot.run(
        _ce,
        _budgets,
        _get_s3(),
        account_id=os.environ["AWS_ACCOUNT_ID"],
        budget_name=os.environ["PROJECT_BUDGET_NAME"],
        today=datetime.now(timezone.utc).date(),
        settings=ProducerSettings.from_env(),
        backfill=bool((event or {}).get("backfill")),
    )
    logger.info("Cost snapshot: %s", json.dumps(summary))
    return summary
