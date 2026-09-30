"""
NCAA MBB normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the ncaambb/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN JSON written
by ncaambb-ingest/ncaambb-schedule-sync from S3, normalizes it into the
project schema, and upserts the results into DynamoDB. Never calls ESPN
directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern). NCAA MBB's raw payloads are single
dicts per object (ESPN's site API), same shape as NBA's:
    ncaambb/teams.json               -> team entities
    ncaambb/scoreboard/{date}.json   -> event records
    ncaambb/boxscore/{season}/{event_id}.json -> player stats, player entities, team stats
    ncaambb/roster/{team_id}.json    -> player entities (team_id correction --
                                         see aws-lambdas/ncaambb/ingest/handler.py's
                                         own docstring for why this is fetched
                                         daily)

Reuses library.normalize.espn's shared normalizers directly (no separate
library/normalize/ncaambb.py module): roster_to_player_entities handles
NCAA MBB's flat (ungrouped) athletes list (same shape as NBA's, not
NFL's grouped shape), and boxscore_to_player_game_stats handles NCAA
MBB's single unnamed stat category without fabricating a "misc" prefix
that would otherwise corrupt TARGET_STAT field names like
"points"/"rebounds". compound_key_splits below is NCAA MBB's own map --
uses the exact same raw ESPN stat keys as NBA's own box score
("fieldGoalsMade-fieldGoalsAttempted" etc.), so the values are identical
to NBA's, but this stays its own dict (not imported from nba/normalize)
per this project's per-sport duplication convention for sport-specific
assembly.
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
logger = logging.getLogger("ncaambb-normalize")

SPORT = "ncaambb"

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
