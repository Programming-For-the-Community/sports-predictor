"""
PGA live-score cache Lambda. Two distinct triggers, one function:

  - EventBridge Scheduler (scheduler-pga-live-scores.tf), every 5
    minutes, event shape {"detail-type": "LiveScoreRefresh"}.
  - API Gateway (REST API, Lambda proxy integration), GET /pga/live-
    scores, behind the same Cognito authorizer as every other PGA route.

Its own Lambda/IAM role -- a dedicated function keeps ingest's daily
batch shape and predict-read's light cold-start shape both unchanged,
same reasoning as every other sport's own live-scores Lambda.
"""
import json
import logging
import os

import boto3

import live_scores
from library.aws import lambda_singletons
from library.aws.boto_config import DEFAULT_CONFIG
from library.http.pga import PGAClient
from library.serving.live_scores_handler import make_lambda_handler
from library.storage.feature_storage import FeatureStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("pga-live-scores")

SPORT = "pga"
RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
}

# Initialized once per container lifetime, reused across warm invocations.
_s3 = boto3.client("s3", config=DEFAULT_CONFIG)
_storage: FeatureStorage | None = None


def _get_storage() -> FeatureStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", FeatureStorage)


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "headers": _CORS_HEADERS, "body": json.dumps(body)}


lambda_handler = make_lambda_handler(SPORT, PGAClient, _get_storage, _s3, RAW_BUCKET, live_scores, logger, _response)
