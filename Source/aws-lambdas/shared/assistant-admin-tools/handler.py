"""
Assistant admin-tools Lambda: runs the admin agent's cost and traffic tools
(library.assistant.admin_tools). Invoked only by the admin agent's runtime
role (Terraform/iam-assistant-agents.tf). Reads the producers' S3 aggregates,
CloudWatch metrics, the events table (pipeline health), the assistant table's
usage rows, the sport registry and the training orchestrator's runs; it holds no Cost Explorer or log-read
permission.

Request: {"tool": "<name>", "input": {...}}
Response: {"ok": true, "result": {...}} or {"ok": false, "error": "..."}.
"""
import logging
import os

import boto3

from library.assistant import admin_tools
from library.assistant.settings import ToolSettings
from library.assistant.sports import load_sports
from library.aws.boto_config import DEFAULT_CONFIG
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("assistant-admin-tools")

_cloudwatch = boto3.client("cloudwatch", config=DEFAULT_CONFIG)
_lambda = boto3.client("lambda", config=DEFAULT_CONFIG)
_stepfunctions = boto3.client("stepfunctions", config=DEFAULT_CONFIG)
_dynamodb = boto3.resource("dynamodb", config=DEFAULT_CONFIG)
_events = _dynamodb.Table(os.environ["EVENTS_TABLE_NAME"])
_assistant = _dynamodb.Table(os.environ["ASSISTANT_TABLE_NAME"])
_registry = _dynamodb.Table(os.environ["SPORT_REGISTRY_TABLE_NAME"])
_s3: S3Manager | None = None


def _get_s3() -> S3Manager:
    global _s3
    if _s3 is None:
        _s3 = S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION"))
    return _s3


def lambda_handler(event, context):
    name = (event or {}).get("tool", "")
    try:
        tools_context = admin_tools.AdminContext(
            s3=_get_s3(), settings=ToolSettings.from_env(), cloudwatch=_cloudwatch, lambda_client=_lambda,
            api_name=os.environ["API_GATEWAY_NAME"], project=os.environ["PROJECT_NAME"],
            events_table=_events, assistant_table=_assistant, sports=lambda: load_sports(_registry),
            stepfunctions=_stepfunctions, training_state_machine_arn=os.environ["TRAINING_STATE_MACHINE_ARN"],
        )
        result = admin_tools.run(name, (event or {}).get("input"), tools_context)
    except ValueError as error:  # includes ToolInputError: a bad tool call
        logger.info("Rejected %s: %s", name, error)
        return {"ok": False, "error": str(error)}
    except Exception:
        logger.exception("Tool %s failed", name)
        return {"ok": False, "error": "The tool failed. Try again shortly."}
    logger.info("Ran %s", name)
    return {"ok": True, "result": result}
