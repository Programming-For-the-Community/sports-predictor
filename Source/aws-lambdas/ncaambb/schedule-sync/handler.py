"""
NCAA MBB schedule-sync Lambda. Triggered directly by EventBridge Scheduler
-- see Terraform/scheduler-ncaambb-schedule-sync.tf. In ONE invocation,
walks up to SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar dates from today and
writes each date's scoreboard to S3 under the exact same
ncaambb/scoreboard/{date}.json key ingest/handler.py already uses -- the
existing normalize Lambda's S3 trigger picks these up and upserts events
into DynamoDB automatically, same as daily ingest, no new normalize code
needed.

Exists because daily ingest (aws-lambdas/ncaambb/ingest/handler.py) only
ever fetches yesterday's date per run -- nothing seeds future dates ahead
of time, so the frontend's upcoming-events list would otherwise only ever
show whatever ingest happened to backfill after the fact, one day late.

Deliberately does not fetch box scores or rosters -- meaningless days/weeks
ahead of a game that hasn't been played. Daily ingest remains the only
source of box-score/roster fetches. Unlike ingest's own roster/box-score
loops, this Lambda's per-invocation volume (one scoreboard call per date,
not per game) doesn't scale with NCAA MBB's ~150-game-Saturday volume --
it's architecturally identical to NBA's own schedule-sync regardless of
how many games land on any one of the dates it walks, so it stays
sequential rather than needing the ThreadPoolExecutor ingest/handler.py
uses (see that module's own VOLUME docstring section).

ONE shared NCAAMBBClient for the whole run, not a separate client per
date -- every one of this run's own ESPN requests is paced by the SAME
RateLimiter instance, so nothing here can burst past ESPN's rate limit
regardless of how many dates are being synced.

There is no preseason concept to skip here -- confirmed live, 2026-08-19
(see project-ncaambb-onboarding memory and ingest/handler.py's own
docstring): ESPN's NCAA MBB scoreboard has zero events before the real
regular-season start date, and the earliest games of a season already
carry season.type=2/"regular-season".

Full-season walk with an idempotent skip-if-already-synced:
season_projection.py's own remaining_games input needs the whole rest of
the season seeded in DynamoDB, not just the next two weeks.
SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS is a generous fixed ceiling, not a real
season-end lookup -- walking past the actual National Championship just
costs a few empty-events calls.

The idempotent skip (_object_exists) is what keeps this affordable on a
daily schedule despite the large ceiling: after the season is fully
seeded, every run only ever calls ESPN for dates it hasn't already
written -- in steady state, just the one new day the rolling window added
since yesterday.

RESCHEDULE HANDLING: the idempotent skip above only applies beyond
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS -- every date inside that near-term
window is re-fetched and overwritten on every run, regardless of whether
it was already written. A rescheduled game moves both its old date's
entry (now stale, still lists a game that no longer plays there) and its
new date's entry (missing, if that date was already synced before the
move happened) -- neither self-corrects under a pure idempotent skip;
ESPN's own scoreboard response is the only source of truth for "what's
actually on this date now" and has to be re-asked. Scoped to the
near-term window, not every date: re-fetching all ~270 dates daily would
cost ~270 ESPN calls/run for a class of change (reschedule) that's rare
and virtually never announced more than a few weeks out -- a date
already outside the window when this runs will have entered it (and been
caught) well before it's played. Beyond the window, a date still
ultimately self-corrects once the game is actually played: daily
ingest's own "yesterday" fetch always writes that day's real
scoreboard/box score unconditionally, regardless of what schedule-sync
already wrote for it -- the refresh window just makes that correction
happen days/weeks earlier, before the game, not only after.

CONFERENCE MEMBERSHIP: also resolves and caches ESPN's live conference-
group hierarchy (library.http.ncaambb_core.resolve_conference_membership)
to ncaambb/conference-membership/{season}.json every run. This lives here
-- not in predict/season_projection.py, which actually consumes it --
because predict/'s own Lambda is VPC-attached with no route to the public
internet at all (no NAT Gateway, see docs/ARCHITECTURE.md; its security
group only opens 443 to the S3/DynamoDB VPC Gateway Endpoints' own prefix
lists), while this Lambda is NOT VPC-attached and already has ordinary
internet egress -- same reasoning ingest/handler.py's own ESPN calls
already rely on. season_projection.py reads this cached object via
FeatureStorage's underlying S3 read access (scoped to this one prefix, see
iam-lambda-inference.tf) instead of calling ESPN itself.

The same resolved mapping is also written onto each team entity's own
metadata.conference (library.serving.common.enrich_participants reads it
from there, generically, the same way it already does for every other
sport) -- a team whose entity doesn't exist yet is skipped rather than
created as a stub; it picks up its conference the next time this Lambda
runs, after normalize has created the entity from that team's own first
synced game.

The season number used for the cache key is a simple calendar heuristic
(August or later -> next calendar year, matching ESPN's own "the season
is labeled by the year its second half falls in" convention), not
authoritative -- season_projection.py cross-checks against the real season
number on actual ingested events and simply treats a season-number
mismatch (or a missing cache object) as a transient miss, self-correcting
on the next scheduled run, the same resilience story every other
eventually-consistent piece of this pipeline already has.
"""
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.aws.dynamodb_table import DynamoDBTable
from library.http.ncaambb import NCAAMBBClient
from library.http.ncaambb_core import NCAAMBBCoreClient, resolve_conference_membership
from library.schema.keys import entity_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaambb-schedule-sync")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
_entities: DynamoDBTable | None = None


def _get_entities() -> DynamoDBTable:
    global _entities
    if _entities is None:
        _entities = DynamoDBTable(os.environ["ENTITIES_TABLE_NAME"], region=os.environ.get("AWS_REGION"))
    return _entities

# Generous fixed ceiling covering the whole regular season plus
# conference/NCAA tournaments with real padding on both ends -- not a real
# season-end lookup. Combined with the idempotent skip below, walking a
# bit past the actual end of the season just costs a few cheap
# empty-events calls. Same value as NBA's own ceiling -- both are just
# "generous," not tied precisely to either sport's actual season length.
SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS = 270

# Every date inside this window is re-fetched every run regardless of the
# idempotent skip below -- see this module's own RESCHEDULE HANDLING
# docstring section. 14 days is a deliberate balance: cheap (14 extra
# ESPN calls/run) while still catching a reschedule with real advance
# notice before the game, not just after it's already happened.
SCHEDULE_SYNC_REFRESH_WINDOW_DAYS = 14

_s3 = boto3.client("s3")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")


def _current_ncaambb_season(today: date) -> int:
    """Calendar heuristic, not authoritative -- see this module's own
    CONFERENCE MEMBERSHIP docstring section."""
    return today.year + 1 if today.month >= 8 else today.year


def _sync_team_conference_metadata(team_conference: dict[str, str]) -> int:
    updated = 0
    entities = _get_entities()
    for team_id, conference in team_conference.items():
        entity = entities.get_item({"entity_key": entity_key("ncaambb", team_id, "team")})
        if entity is None:
            continue
        if entity.get("metadata", {}).get("conference") == conference:
            continue
        entities.put_item({**entity, "metadata": {**entity.get("metadata", {}), "conference": conference}})
        updated += 1
    return updated


def _sync_conference_membership(season: int) -> None:
    core_client = NCAAMBBCoreClient()
    team_conference = resolve_conference_membership(core_client, season)
    _put_json(f"ncaambb/conference-membership/{season}.json", {"season": season, "team_conference": team_conference})
    updated = _sync_team_conference_metadata(team_conference)
    logger.info(
        "Cached conference membership for season %d (%d teams, %d entities updated)",
        season, len(team_conference), updated,
    )


def lambda_handler(event: dict, context) -> dict:
    start = date.today()
    client = NCAAMBBClient()

    try:
        _sync_conference_membership(_current_ncaambb_season(start))
    except Exception:
        # A failed refresh leaves yesterday's cached object in place (or,
        # early in a brand-new season, no object at all) -- either way
        # season_projection.py's own read degrades gracefully, same as
        # every other cache-miss case in this pipeline. Doesn't block the
        # date-walk below.
        logger.exception("Failed refreshing cached NCAA MBB conference membership")

    synced = refreshed = skipped = failed = 0
    for offset in range(SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS):
        target_date = (start + timedelta(days=offset)).strftime("%Y%m%d")
        scoreboard_key = f"ncaambb/scoreboard/{target_date}.json"

        already_written = _object_exists(scoreboard_key)
        in_refresh_window = offset < SCHEDULE_SYNC_REFRESH_WINDOW_DAYS
        if already_written and not in_refresh_window:
            skipped += 1
            continue

        try:
            scoreboard = client.get_scoreboard_for_date(target_date)
            _put_json(scoreboard_key, scoreboard)
            # A refresh-window overwrite still re-triggers normalize's own
            # S3 PUT trigger the exact same way a first-time write does --
            # this only distinguishes the two for observability (how many
            # dates actually had a schedule change worth re-fetching vs.
            # how many were newly seeded), not for any behavior difference.
            if already_written:
                refreshed += 1
            else:
                synced += 1
        except Exception:
            # One date's transient ESPN failure doesn't block the rest of
            # the lookahead window from syncing -- tomorrow's scheduled
            # run retries it anyway (either its S3 object was never
            # written, so the idempotent skip won't hide the retry, or
            # it's still inside the refresh window and gets retried
            # unconditionally regardless).
            logger.exception("Failed syncing date %s", target_date)
            failed += 1

    logger.info(
        "Schedule sync complete: %d synced, %d refreshed, %d skipped, %d failed", synced, refreshed, skipped, failed,
    )
    return {"synced": synced, "refreshed": refreshed, "skipped": skipped, "failed": failed}
