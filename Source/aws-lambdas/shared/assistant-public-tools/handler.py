"""
Assistant public-tools Lambda: runs the public agent's read tools
(library.assistant.public_tools). Invoked only by the two assistant agents'
runtime roles (Terraform/iam-assistant-agents.tf); never by API Gateway.

Request: {"tool": "<name>", "input": {...}}
Response: {"ok": true, "result": {...}} or {"ok": false, "error": "..."} --
an error is a message for the model, not an exception.

Sports are the sport registry's (library.assistant.sports). Every tool
reads through predict-read, the Lambda behind the app's own
routes, with an API-Gateway-shaped request.
"""
import json
import logging
import os

import boto3
from botocore.config import Config

from library.assistant import public_tools
from library.assistant.settings import ToolSettings
from library.assistant.sports import load_sports
from library.assistant.tool_specs import ToolInputError
from library.aws.boto_config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("assistant-public-tools")

# predict-read can take a few seconds on a cold cache.
_lambda = boto3.client("lambda", config=DEFAULT_CONFIG.merge(Config(read_timeout=30)))
_registry = boto3.resource("dynamodb", config=DEFAULT_CONFIG).Table(os.environ["SPORT_REGISTRY_TABLE_NAME"])


def _read_predict(resource: str, path_params: dict, query_params: dict) -> tuple[int, dict]:
    request = {"resource": resource, "httpMethod": "GET", "pathParameters": path_params, "queryStringParameters": query_params}
    response = _lambda.invoke(FunctionName=os.environ["PREDICT_READ_FUNCTION_NAME"], Payload=json.dumps(request).encode())
    payload = json.loads(response["Payload"].read() or b"{}")
    body = payload.get("body") or "{}"
    return int(payload.get("statusCode", 500)), json.loads(body) if isinstance(body, str) else body


def lambda_handler(event, context):
    name = (event or {}).get("tool", "")
    try:
        result = public_tools.run(name, (event or {}).get("input"), _read_predict, load_sports(_registry), ToolSettings.from_env())
    except (ToolInputError, ValueError) as error:
        logger.info("Rejected %s: %s", name, error)
        return {"ok": False, "error": str(error)}
    except Exception:
        logger.exception("Tool %s failed", name)
        return {"ok": False, "error": "The tool failed. Try again shortly."}
    logger.info("Ran %s", name)
    return {"ok": True, "result": result}
