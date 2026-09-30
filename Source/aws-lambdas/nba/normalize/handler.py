"""
NBA normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the nba/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN JSON written
by nba-ingest/nba-schedule-sync from S3, normalizes it into the project
schema, and upserts the results into DynamoDB. Never calls ESPN directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern). NBA's raw payloads are single
dicts per object (ESPN's site API):
    nba/teams.json               -> team entities
    nba/scoreboard/{date}.json   -> event records
    nba/boxscore/{season}/{event_id}.json -> player stats, player entities, team stats
    nba/roster/{team_id}.json    -> player entities (team_id correction --
                                     see aws-lambdas/nba/ingest/handler.py's
                                     own docstring for why this is fetched
                                     daily)

Reuses library.normalize.espn's shared normalizers directly (no separate
library/normalize/nba.py module): roster_to_player_entities handles NBA's
flat (ungrouped) athletes list, and boxscore_to_player_game_stats handles
NBA's single unnamed stat category without fabricating a "misc" prefix
that would otherwise corrupt TARGET_STAT field names like
"points"/"rebounds". compound_key_splits below is NBA's own map -- one
shared dict for both team- and player-level box scores, since NBA's
"made-attempted" compound keys are identical strings at both levels.
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
logger = logging.getLogger("nba-normalize")

SPORT = "nba"

_COMPOUND_KEY_SPLITS = espn.BASKETBALL_COMPOUND_KEY_SPLITS

_s3 = boto3.client("s3", config=DEFAULT_CONFIG)
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", PipelineStorage)


_normalizer = EspnNormalizer(
    SPORT, _get_storage, logger,
    player_compound_key_splits=_COMPOUND_KEY_SPLITS, team_compound_key_splits=_COMPOUND_KEY_SPLITS,
)
_clear_departed_players = _normalizer.clear_departed_players
_process_boxscore = _normalizer.process_boxscore


def _dispatch(bucket: str, key: str) -> None:
    _normalizer.dispatch(_s3, bucket, key)


def lambda_handler(event: dict, context) -> dict:
    return dispatch_common.lambda_handler_body(event, _dispatch, logger)
