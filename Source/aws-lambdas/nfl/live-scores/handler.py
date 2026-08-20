"""
NFL live-score cache Lambda. Two distinct triggers, one function:

  - EventBridge Scheduler (scheduler-nfl-live-scores.tf), every 60s, event
    shape {"detail-type": "LiveScoreRefresh"} -- checks events near their
    kickoff window against ESPN's live scoreboard and caches the result to
    S3.
  - API Gateway (REST API, Lambda proxy integration), GET /nfl/live-scores,
    behind the same Cognito authorizer as every other NFL route -- serves
    whatever live_scores.refresh last cached.
"""
import json
import logging
import os

import boto3

import live_scores
from library.http.nfl import NFLClient
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nfl-live-scores")

SPORT = "nfl"
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
        return live_scores.refresh(_get_storage(), _s3, RAW_BUCKET, NFLClient(), SPORT)

    resource = event.get("resource", "")

    try:
        if resource == "/nfl/live-scores":
            return _response(200, live_scores.get_live_scores(_s3, RAW_BUCKET))

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
