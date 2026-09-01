"""
F1 live-score cache Lambda. Two distinct triggers, one function (same
shape as PGA's own aws-lambdas/pga/live-scores/handler.py):

  - EventBridge Scheduler (scheduler-f1-live-scores.tf), every 3 minutes,
    event shape {"detail-type": "LiveScoreRefresh"}.
  - API Gateway (REST API, Lambda proxy integration), GET /f1/live-scores,
    behind the same Cognito authorizer as every other F1 route.

Its own Lambda/IAM role -- a dedicated function keeps ingest's daily
batch shape and predict-read's light cold-start shape both unchanged,
same reasoning as every other sport's own live-scores Lambda. See
live_scores.py's own docstring for why this is ESPN-sourced even though
every other F1 Lambda is Jolpica-sourced.
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3

import live_scores
from library.http.f1_espn import F1EspnClient
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("f1-live-scores")

SPORT = "f1"
RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Initialized once per container lifetime, reused across warm invocations.
_s3 = boto3.client("s3")
_storage: FeatureStorage | None = None


def _get_storage() -> FeatureStorage:
    global _storage
    if _storage is None:
        _storage = FeatureStorage()
    return _storage


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def lambda_handler(event, context):
    if event.get("detail-type") == "LiveScoreRefresh":
        # F1 seasons run within a single calendar year (unlike NBA/NHL),
        # so "current year" is always the right season to poll.
        season = datetime.now(timezone.utc).year
        return live_scores.refresh(_get_storage(), _s3, RAW_BUCKET, F1EspnClient(), SPORT, season)

    resource = event.get("resource", "")

    try:
        if resource == "/f1/live-scores":
            return _response(200, live_scores.get_live_scores(_s3, RAW_BUCKET))

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
