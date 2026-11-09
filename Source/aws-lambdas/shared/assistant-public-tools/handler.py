"""
Assistant public-tools Lambda: runs the public agent's read tools
(library.assistant.public_tools). Invoked only by the two assistant agents'
runtime roles (Terraform/iam-assistant-agents.tf); never by API Gateway.

Request: {"tool": "<name>", "input": {...}}
Response: {"ok": true, "result": {...}} or {"ok": false, "error": "..."} --
an error is a message for the model, not an exception.

Sports are the sport registry's (library.assistant.sports). Every tool
reads through predict-read, the Lambda behind the app's own
routes, with an API-Gateway-shaped request; get_live_scores also reads the
live-score cache each sport's live-scores Lambda keeps in the raw bucket.
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from library.assistant import public_tools
from library.assistant.settings import ToolSettings
from library.assistant.sports import load_sports
from library.aws.boto_config import DEFAULT_CONFIG
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("assistant-public-tools")

# predict-read can take a few seconds on a cold cache.
_lambda = boto3.client("lambda", config=DEFAULT_CONFIG.merge(Config(read_timeout=30)))
_registry = boto3.resource("dynamodb", config=DEFAULT_CONFIG).Table(os.environ["SPORT_REGISTRY_TABLE_NAME"])
_raw: S3Manager | None = None
# Live caches older than this are from a finished poll window, not live play.
LIVE_STALE_SECONDS = 300


def _read_predict(resource: str, path_params: dict, query_params: dict) -> tuple[int, dict]:
    request = {"resource": resource, "httpMethod": "GET", "pathParameters": path_params, "queryStringParameters": query_params}
    response = _lambda.invoke(FunctionName=os.environ["PREDICT_READ_FUNCTION_NAME"], Payload=json.dumps(request).encode())
    payload = json.loads(response["Payload"].read() or b"{}")
    body = payload.get("body") or "{}"
    return int(payload.get("statusCode", 500)), json.loads(body) if isinstance(body, str) else body


def _read_live(sport: str) -> dict | None:
    """The sport's live-score cache, or None when it has none or it's stale.
    The role reads only those cache keys, so a sport that never polled
    answers 403, not 404."""
    global _raw
    _raw = _raw or S3Manager(os.environ["RAW_DATA_BUCKET_NAME"])
    try:
        cache = _raw.get_json(f"{sport}/cache/live-scores/latest.json")
    except ClientError:
        return None
    fetched = datetime.fromisoformat(cache.get("fetched_at", "1970-01-01T00:00:00+00:00"))
    return cache if (datetime.now(timezone.utc) - fetched).total_seconds() <= LIVE_STALE_SECONDS else None


def lambda_handler(event, context):
    name = (event or {}).get("tool", "")
    try:
        result = public_tools.run(name, (event or {}).get("input"), _read_predict, load_sports(_registry), ToolSettings.from_env(), _read_live)
    except ValueError as error:  # includes ToolInputError: a bad tool call
        logger.info("Rejected %s: %s", name, error)
        return {"ok": False, "error": str(error)}
    except Exception:
        logger.exception("Tool %s failed", name)
        return {"ok": False, "error": "The tool failed. Try again shortly."}
    logger.info("Ran %s", name)
    return {"ok": True, "result": result}
