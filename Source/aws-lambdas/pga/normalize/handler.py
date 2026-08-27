"""
PGA normalize Lambda. Triggered by S3 PutObject events on the raw data
lake, filtered to the pga/ prefix (see
Terraform/s3-raw-data-lake-notifications.tf). Reads raw ESPN leaderboard
JSON written by pga-ingest/pga-schedule-sync from S3, normalizes it into
the project schema, and upserts the results into DynamoDB. Never calls
ESPN directly.

Only one raw payload shape exists for PGA, unlike every head-to-head
sport's normalize Lambda (which routes teams.json/scoreboard/boxscore/
roster keys separately): pga/leaderboard/{season}/{event_id}.json is the
ONLY key pattern either ingest or schedule-sync ever writes -- one
leaderboard fetch already carries both the tournament (event) and every
competitor's own entity data, since there's no separate roster source for
golf (see library/normalize/pga.py's own docstring) and no player_game_
stats table for a field-event sport (design/DATA_SCHEMA.md) -- so there's
nothing else to route.

One Lambda invocation may receive multiple S3 records if notifications
are batched. Each record is processed independently so a failure in one
doesn't block the others.
"""
import json
import logging
import urllib.parse

import boto3

from library.normalize.pga import is_flat_stroke_play, leaderboard_event_to_event_item, leaderboard_event_to_player_entities
from library.normalize.pga_matchplay import (
    is_exhibition,
    is_supported_match_play,
    leaderboard_event_to_cup_event_item,
    leaderboard_event_to_match_event_items,
    leaderboard_event_to_matchplay_player_entities,
    leaderboard_event_to_matchplay_team_entities,
)
from library.storage.pipeline_storage import PipelineStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("pga-normalize")

SPORT = "pga"

_s3 = boto3.client("s3")
_storage: PipelineStorage | None = None


def _get_storage() -> PipelineStorage:
    # Initialized once per container lifetime, reused across warm invocations.
    global _storage
    if _storage is None:
        _storage = PipelineStorage()
    return _storage


def _process_match_play_leaderboard(event: dict, key: str) -> None:
    """team_match_play (Ryder Cup/Presidents Cup) or individual_match_play
    (WGC-Dell Technologies Match Play) -- see library.normalize.
    pga_matchplay's own module docstring and data-backfills/pga/
    backfill.py's matching _process_match_play_tournament, which this
    mirrors for the ongoing daily-ingest pipeline."""
    match_items = leaderboard_event_to_match_event_items(event, SPORT)
    if not match_items:
        logger.warning(
            "Event %s in %s is Match scoring but has no individual match data (status=%s) -- a real ESPN gap, not written.",
            event.get("id"), key, (event.get("status") or {}).get("type", {}).get("name"),
        )
        return

    storage = _get_storage()
    team_entities = leaderboard_event_to_matchplay_team_entities(event, SPORT)
    player_entities = leaderboard_event_to_matchplay_player_entities(event, SPORT)
    for entity in team_entities:
        storage.upsert_entity(entity)
    for entity in player_entities:
        storage.upsert_entity(entity)

    cup_item = leaderboard_event_to_cup_event_item(event, SPORT)
    if cup_item is not None:
        storage.upsert_event(cup_item)
    for match_item in match_items:
        storage.upsert_event(match_item)

    logger.info(
        "Upserted %d match event(s)%s and %d entity(ies) from %s",
        len(match_items), " + 1 cup event" if cup_item is not None else "",
        len(team_entities) + len(player_entities), key,
    )


def _process_leaderboard(payload: dict, key: str) -> None:
    storage = _get_storage()
    events = payload.get("events", [])
    if not events:
        logger.warning("No events in leaderboard payload %s", key)
        return
    event = events[0]

    # Ryder Cup/Presidents Cup (team_match_play) and WGC-Dell Technologies
    # Match Play (individual_match_play) -- routed through their own
    # normalizer module, not library.normalize.pga's flat-stroke-play one
    # below. See library.normalize.pga_matchplay's own module docstring.
    if is_supported_match_play(event):
        _process_match_play_leaderboard(event, key)
        return

    # The Match -- a made-for-TV exhibition sharing Ryder Cup's own
    # team+roster shape but with no real Cup-level result and no
    # guarantee its "athletes" are even PGA Tour golfers (see
    # library.normalize.pga_matchplay.is_exhibition's own docstring).
    # Excluded permanently, not deferred.
    if is_exhibition(event):
        logger.info(
            "Skipping %s -- exhibition, not a real competitive tournament (tournament=%r)",
            key, (event.get("tournament") or {}).get("displayName"),
        )
        return

    # Neither a supported flat-stroke-play format (Medal/Teamstroke) nor
    # a supported match-play format -- an unrecognized future scoring
    # system, or a not-yet-populated calendar entry. Fail closed rather
    # than guess a shape. Raw JSON for these is still written to S3 by
    # ingest/schedule-sync (harmless, preserves the record), it just
    # never reaches DynamoDB.
    if not is_flat_stroke_play(event):
        logger.info(
            "Skipping %s -- unrecognized scoring system (tournament=%r, scoringSystem=%r)",
            key, (event.get("tournament") or {}).get("displayName"),
            (event.get("tournament") or {}).get("scoringSystem", {}).get("name"),
        )
        return

    # A real, confirmed ESPN gap distinct from the above -- a Medal/
    # Teamstroke event whose own competition object has no "competitors"
    # key at all (confirmed live, 2026-08-26, on a small cluster of 2020
    # COVID-canceled tournaments plus a few real completed ones ESPN never
    # populated -- see data-backfills/pga/backfill.py's matching check and
    # design/DATA_SCHEMA.md for the full writeup). Writing this event
    # anyway would corrupt the cutline dataset's field_size feature
    # (len(participants)) to 0 for a real full field, so it's skipped
    # entirely -- same treatment as the no-events-in-payload case above.
    competition = event["competitions"][0] if event.get("competitions") else {}
    if not competition.get("competitors"):
        logger.warning(
            "Event %s in %s is stroke-play scoring but has no competitor data (status=%s) -- a real ESPN gap, not written.",
            event.get("id"), key, (event.get("status") or {}).get("type", {}).get("name"),
        )
        return

    # Entities upserted before the event that references them, same
    # ordering every head-to-head normalizer uses -- not load-bearing
    # (DynamoDB has no foreign-key enforcement), but keeps a reader that
    # queries right after this run from ever seeing an event whose
    # participants' entities don't exist yet. Plain upsert_entity, not
    # upsert_player_entity -- that guard is specifically for team_id
    # staleness, a concept golfers (no team) don't have.
    entities = leaderboard_event_to_player_entities(event, SPORT)
    for entity in entities:
        storage.upsert_entity(entity)

    storage.upsert_event(leaderboard_event_to_event_item(event, SPORT))
    logger.info("Upserted 1 event and %d player entities from %s", len(entities), key)


def _dispatch(bucket: str, key: str) -> None:
    response = _s3.get_object(Bucket=bucket, Key=key)
    payload = json.loads(response["Body"].read())

    if "/leaderboard/" in key:
        _process_leaderboard(payload, key)
    else:
        logger.warning("Unrecognized S3 key pattern, skipping: %s", key)


def lambda_handler(event: dict, context) -> dict:
    records = event.get("Records", [])
    processed = failed = 0

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        try:
            _dispatch(bucket, key)
            processed += 1
        except Exception:
            logger.exception("Failed processing s3://%s/%s", bucket, key)
            failed += 1

    return {"processed": processed, "failed": failed}
