"""
Shared S3-key-pattern routing shell + per-record iteration loop for an
ESPN-shaped normalize Lambda (nfl/nba/ncaambb -- confirmed identical
`_dispatch`/`lambda_handler` bodies before sharing here). ncaafb/pga/f1 use
genuinely different raw-payload shapes (CFBD bulk-list, single
`leaderboard/` key, three ESPN/Jolpica prefixes) with their own bespoke
`library.normalize.*` modules and are NOT consolidated here.

Deliberately does NOT hold the 4 processor functions themselves
(process_teams/process_scoreboard/process_boxscore/process_roster) --
those stay defined in each sport's own handler.py, because they call
library.normalize.espn's normalizer functions (team_to_entity,
scoreboard_event_to_event_item, boxscore_to_player_game_stats, ...) via
names each sport's own test suite patches by attribute on THAT sport's own
module (e.g. `patch.object(nfl_normalize, "team_to_entity", ...)`). A
shared-module implementation calling `library.normalize.espn.
team_to_entity(...)` directly would resolve against a different module's
own __dict__ and silently ignore that patch, running the real function
against fake test data instead of the mock. Only the routing/loop shell
below has no such dependency.
"""
import json
import logging
import urllib.parse

from library.aws.account import get_account_id


def dispatch(s3, bucket: str, key: str, logger: logging.Logger, *, process_teams, process_scoreboard, process_boxscore, process_roster) -> None:
    response = s3.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=get_account_id())
    payload = json.loads(response["Body"].read())

    if key.endswith("/teams.json"):
        process_teams(payload, key)
    elif "/scoreboard/" in key:
        process_scoreboard(payload, key)
    elif "/boxscore/" in key:
        process_boxscore(payload, key)
    elif "/roster/" in key:
        process_roster(payload, key)
    else:
        logger.warning("Unrecognized S3 key pattern, skipping: %s", key)


def lambda_handler_body(event: dict, dispatch_fn, logger: logging.Logger) -> dict:
    records = event.get("Records", [])
    processed = failed = 0

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        try:
            dispatch_fn(bucket, key)
            processed += 1
        except Exception:
            logger.exception("Failed processing s3://%s/%s", bucket, key)
            failed += 1

    return {"processed": processed, "failed": failed}
