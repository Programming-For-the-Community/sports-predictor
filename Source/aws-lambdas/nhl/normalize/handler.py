"""
NHL normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the nhl/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN JSON written
by nhl-ingest from S3, normalizes it into the project schema, and upserts
the results into DynamoDB. Never calls ESPN directly.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.

Key routing (based on S3 key pattern):
    nhl/teams.json               -> team entities
    nhl/scoreboard/{date}.json   -> event records
    nhl/boxscore/{season}/{event_id}.json -> player stats, player entities, team stats
    nhl/roster/{team_id}.json    -> player entities

Teams and rosters use the shared EspnNormalizer as is. Scoreboards and
box scores go through library.normalize.nhl instead, for the hockey
event fields (overtime/shootout, probable goalies) and the
skater/goalie box score. Preseason, All-Star and
international-tournament games are skipped.
"""
import logging

import boto3

from library.aws import lambda_singletons
from library.aws.boto_config import DEFAULT_CONFIG
from library.normalize import dispatch as dispatch_common
from library.normalize import nhl
from library.normalize.espn_handler import EspnNormalizer
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("nhl-normalize")

SPORT = "nhl"

_s3 = boto3.client("s3", config=DEFAULT_CONFIG)
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    return lambda_singletons.get_or_create(globals(), "_storage", PipelineStorage)


class NhlNormalizer(EspnNormalizer):
    def process_scoreboard(self, payload: dict, key: str) -> None:
        storage = self._get_storage()
        events = [event for event in payload.get("events", []) if nhl.is_ingestable_event(event)]
        for event in events:
            storage.upsert_event(nhl.scoreboard_event_to_event_item(event, self._sport))
        self._logger.info("Upserted %d events from %s", len(events), key)

    def process_boxscore(self, payload: dict, key: str) -> None:
        if not nhl.is_ingestable_summary(payload):
            self._logger.info("Skipping %s -- not a franchise matchup", key)
            return
        storage = self._get_storage()
        stats_items, player_entities = nhl.boxscore_to_player_game_stats(payload, self._sport)
        for entity in player_entities:
            storage.upsert_player_entity(entity)
        storage.write_player_game_stats(stats_items)
        self._logger.info(
            "Wrote %d player stat lines and %d player entities from %s", len(stats_items), len(player_entities), key,
        )

        team_stats_items = nhl.boxscore_to_team_game_stats(payload, self._sport)
        storage.write_team_game_stats(team_stats_items)
        self._logger.info("Wrote %d team stat lines from %s", len(team_stats_items), key)


_normalizer = NhlNormalizer(SPORT, _get_storage, logger, player_compound_key_splits={}, team_compound_key_splits={})


def _dispatch(bucket: str, key: str) -> None:
    _normalizer.dispatch(_s3, bucket, key)


def lambda_handler(event: dict, context) -> dict:
    return dispatch_common.lambda_handler_body(event, _dispatch, logger)
