"""
Assistant traffic-aggregate Lambda. Invoked hourly by EventBridge Scheduler
(Terraform/scheduler-assistant-producers.tf); counts blocked and denied
requests from the two WAF log groups and the API Gateway access log into
the hourly/daily S3 aggregates the admin assistant's traffic tools read --
see library.assistant.traffic.

Each run rewrites the last TRAFFIC_LOOKBACK_HOURS whole hours, so records WAF
delivers late are counted on the next run. {"backfill_days": 30} rewrites
that many days instead, one query per day per source.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

from library.assistant import traffic
from library.assistant.settings import ProducerSettings
from library.aws.boto_config import DEFAULT_CONFIG
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("assistant-traffic-aggregate")

_logs = boto3.client("logs", config=DEFAULT_CONFIG)
# The CloudFront web ACL's log group lives in us-east-1, with the ACL.
_cloudfront_logs = boto3.client("logs", region_name=os.environ["WAF_CLOUDFRONT_LOG_REGION"], config=DEFAULT_CONFIG)
_s3: S3Manager | None = None


def _get_s3() -> S3Manager:
    global _s3
    if _s3 is None:
        _s3 = S3Manager(os.environ["MODEL_ARTIFACTS_BUCKET_NAME"], region=os.environ.get("AWS_REGION"))
    return _s3


def _sources():
    return [
        ("waf_cloudfront", _cloudfront_logs, os.environ["WAF_CLOUDFRONT_LOG_GROUP"], traffic.WAF_QUERY),
        ("waf_api", _logs, os.environ["WAF_API_GATEWAY_LOG_GROUP"], traffic.WAF_QUERY),
        ("api_gateway", _logs, os.environ["API_GATEWAY_ACCESS_LOG_GROUP"], traffic.ACCESS_QUERY),
    ]


def lambda_handler(event, context):
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    backfill_days = int((event or {}).get("backfill_days") or 0)
    if not backfill_days:
        lookback = ProducerSettings.from_env().traffic_lookback_hours
        summary = traffic.run(_sources(), _get_s3(), end - timedelta(hours=lookback), end)
        logger.info("Traffic aggregate: %s", json.dumps(summary))
        return summary

    totals = {"hours_written": 0, "days_rolled": [], "blocked": 0}
    day_end = end
    for _ in range(backfill_days):
        day_start = day_end - timedelta(days=1)
        summary = traffic.run(_sources(), _get_s3(), day_start, day_end)
        totals["hours_written"] += summary["hours_written"]
        totals["blocked"] += summary["blocked"]
        totals["days_rolled"] = sorted(set(totals["days_rolled"]) | set(summary["days_rolled"]))
        day_end = day_start
    logger.info("Traffic backfill: %s", json.dumps(totals))
    return totals
