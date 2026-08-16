"""
NBA live-score cache Lambda -- the NBA equivalent of aws-lambdas/nfl/
live-scores/handler.py and aws-lambdas/ncaafb/live-scores/handler.py. Two
distinct triggers, one function:

  - EventBridge Scheduler (scheduler-nba-live-scores.tf), every 60s,
    event shape {"detail-type": "LiveScoreRefresh"}.
  - API Gateway (REST API, Lambda proxy integration), GET /nba/live-
    scores, behind the same Cognito authorizer as every other NBA route.

Its own Lambda/IAM role, same reasoning as NFL's/NCAAFB's own modules:
zero impact on ingest's daily batch shape or predict-read's light
cold-start shape either way, a dedicated function keeps both unchanged.
"""
import json
import logging
import os

import boto3

import live_scores
from library.http.nba import NBAClient
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nba-live-scores")

SPORT = "nba"
RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Initialized once per container lifetime, reused across warm invocations
# -- same lazy-singleton pattern as nfl-live-scores/handler.py's own.
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
        return live_scores.refresh(_get_storage(), _s3, RAW_BUCKET, NBAClient(), SPORT)

    resource = event.get("resource", "")

    try:
        if resource == "/nba/live-scores":
            return _response(200, live_scores.get_live_scores(_s3, RAW_BUCKET))

        return _response(404, {"error": f"No route for resource {resource!r}"})

    except Exception:
        logger.exception("Unhandled error serving %s", resource)
        return _response(500, {"error": "Internal server error"})
