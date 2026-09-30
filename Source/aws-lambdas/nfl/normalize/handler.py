"""
NFL normalize Lambda. Triggered by S3 PutObject events on the raw data
lake (filtered to the nfl/ prefix -- see Terraform/lambda-nfl-normalize.tf).
Reads raw ESPN JSON from S3, normalizes it to the project schema, and
upserts the results into DynamoDB. Never fetches from ESPN directly.

One Lambda invocation may receive multiple S3 records if notifications are
batched, though in practice a single PUT triggers a single notification.
Each record is processed independently so a failure in one doesn't block
the others.

Key routing (based on S3 key pattern):
    nfl/teams.json                             -> team entities
    nfl/scoreboard/{season}/{type}/{week}.json -> event records
    nfl/boxscore/{season}/{event_id}.json      -> player stats, player entities, team stats
    nfl/roster/{team_id}.json                  -> player entities (team_id correction)
"""
import logging

import boto3

from library.aws import lambda_singletons
from library.aws.boto_config import DEFAULT_CONFIG
from library.normalize import dispatch as dispatch_common
from library.normalize import espn
from library.normalize.espn_handler import EspnNormalizer
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nfl-normalize")

SPORT = "nfl"

_COMPOUND_KEY_SPLITS = espn.FOOTBALL_PLAYER_COMPOUND_KEY_SPLITS

_TEAM_COMPOUND_KEY_SPLITS = espn.FOOTBALL_TEAM_COMPOUND_KEY_SPLITS

_s3 = boto3.client("s3", config=DEFAULT_CONFIG)
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", PipelineStorage)


_normalizer = EspnNormalizer(
    SPORT, _get_storage, logger,
    player_compound_key_splits=_COMPOUND_KEY_SPLITS, team_compound_key_splits=_TEAM_COMPOUND_KEY_SPLITS,
)
_clear_departed_players = _normalizer.clear_departed_players
_process_boxscore = _normalizer.process_boxscore


def _dispatch(bucket: str, key: str) -> None:
    _normalizer.dispatch(_s3, bucket, key)


def lambda_handler(event: dict, context) -> dict:
    return dispatch_common.lambda_handler_body(event, _dispatch, logger)