"""
NCAA MBB ingest Lambda. Triggered daily by the shared ingest-orchestrator
Step Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention -- no separate per-sport EventBridge Scheduler is needed for
this Lambda. Fetches yesterday's completed games' box scores plus that
same date's scoreboard, the full ~362-team D1 list (ncaambb/teams.json --
what normalize's team_to_entity derives team display name/abbreviation/
color from), and every team's roster, and writes it all as raw JSON to
S3; the normalize Lambda is triggered automatically by the resulting S3
PutObject events, so this function never touches DynamoDB directly.

ESPN's NCAA MBB scoreboard is date-based, not week-based (see
NCAAMBBClient.get_scoreboard_for_date), same as NBA's. Defaults to
yesterday (not today): games mostly tip off in the evening and finish
after this Lambda's own early-morning scheduled run would see them, so
"yesterday" is the date most likely to have final scores/box scores
ready. Today's/future dates are seeded ahead of time by
Terraform/scheduler-ncaambb-schedule-sync.tf's dedicated
ncaambb-schedule-sync Lambda.

EventBridge can override the target date via the orchestrator's input
payload: { "date": "20260114" } (YYYYMMDD, matching NCAAMBBClient's own
param shape) -- for reprocessing one specific past date.

Also refreshes every one of D1's ~362 teams' current full roster on every
run, unconditionally, uncached -- this exists specifically to catch a
roster move as soon as possible, so caching it across days would defeat
its own purpose. No depth-chart concept in basketball, and coach-tenure
features aren't built (same call NBA's own ingest already made). NCAA
MBB's roster response already embeds each athlete's current injury status
directly, same shape as NBA's, so there's no separate injury-report call
needed. Injuries are wired in (_fetch_rosters/_attach_injuries below,
attached onto each scoreboard event before it's written to S3 -- see
_attach_injuries).

Unlike NFL/NBA, there is no preseason concept to skip here -- confirmed
live, 2026-08-19 (see project-ncaambb-onboarding memory): ESPN's NCAA MBB
scoreboard has zero events before the real regular-season start date
(first Monday of November), and the earliest games of a season already
carry season.type=2/"regular-season". No PRESEASON_TYPE filtering exists
in this module for that reason, not because it was overlooked.

Also fetches this week's AP Top 25 poll on every run (see
_fetch_current_ap_poll) -- keeps the national-ranking model's training
data (see project-ncaambb-onboarding memory) current going forward, since
data-backfills/ncaambb/backfill.py's own seed_rankings only ever covers
polls up through whenever it last ran. Best-effort and fully isolated
from the rest of this run -- a ranking-fetch failure never blocks or
fails box-score/roster ingest, a genuinely secondary data source.

VOLUME: unlike NBA's ~30 teams/~15 games-a-night, D1 has ~362 teams (every
one of which gets its roster re-fetched every run) and a single busy
Saturday can carry ~150-155 games (confirmed live, 2026-08-19) -- exactly
the stress case this sub-phase was scoped to handle. A plain sequential
per-item loop (NBA's own shape) would serialize each item's full
request-plus-network-latency cost instead of just its share of the shared
rate limiter's floor, and at this volume that difference is real wall-
clock time, not noise. Both the roster fetch and the box-score fetch below
use a ThreadPoolExecutor of _INGEST_MAX_WORKERS workers sharing ONE
NCAAMBBClient (and therefore one RateLimiter instance -- see
library/http/rate_limiter.py's own docstring: it caps the combined request
rate across every thread using it, the same "shared limiter across
concurrent workers" idiom data-backfills/nba/backfill.py already
established). Concurrency here only hides per-call network latency behind
that shared floor -- it does not increase the actual request rate against
ESPN, since the limiter still serializes every dispatch.
"""
import concurrent.futures
import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

from library.http.ncaambb import NCAAMBBClient
from library.http.ncaambb_core import NCAAMBBCoreClient, current_ap_poll_pointer
from library.normalize.espn import roster_to_team_injuries

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("ncaambb-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]

# I/O-bound HTTP calls sharing one rate-limited client -- see this
# module's own VOLUME docstring section for why this exists at all (NBA's
# equivalent loops stay sequential; its volume never justified this).
_INGEST_MAX_WORKERS = 8

_s3 = boto3.client("s3")


def _yesterday(today: date | None = None) -> str:
    return ((today or date.today()) - timedelta(days=1)).strftime("%Y%m%d")


def _object_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=RAW_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _put_json(key: str, payload: dict) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")
    logger.info("Wrote s3://%s/%s", RAW_BUCKET, key)


def _team_ids(teams_response: dict) -> list[str]:
    """Every D1 team id from a get_teams() response -- not derived from
    any date's scoreboard, so this works identically in the off-season or
    on a night with no games at all."""
    leagues = teams_response.get("sports", [{}])[0].get("leagues", [{}])
    teams = leagues[0].get("teams", []) if leagues else []
    return [t["team"]["id"] for t in teams if t.get("team", {}).get("id")]


def _fetch_one_roster(client: NCAAMBBClient, team_id: str) -> list[dict]:
    """One team's roster: fetches, writes to S3, and returns its injury
    list. Runs on a worker thread (see _fetch_rosters below); client is
    shared, and its RateLimiter is thread-safe by design
    (library/http/rate_limiter.py). Raises straight through on failure --
    the caller (_fetch_rosters) reads it off the Future and handles it
    there, same as ThreadPoolExecutor's normal exception-propagation
    contract."""
    roster = client.get_roster(team_id)
    _put_json(f"ncaambb/roster/{team_id}.json", roster)
    return roster_to_team_injuries(roster)


def _fetch_rosters(client: NCAAMBBClient, team_ids: list[str]) -> tuple[int, int, dict[str, list[dict]]]:
    """Fetches and writes every D1 team's current roster -- one S3
    object per team, always fresh, never TTL-cached (see this module's own
    docstring for why). Best-effort per team -- one team's fetch failing
    shouldn't lose the others'. Concurrent across _INGEST_MAX_WORKERS
    threads sharing one rate-limited client -- see this module's own
    VOLUME docstring section.

    Also returns injuries_by_team, keyed by team id -- extracted from the
    same roster responses being written to S3 here, no extra API call. A
    team whose own fetch failed simply has no entry, same best-effort
    handling _attach_injuries below applies."""
    fetched = failed = 0
    injuries_by_team: dict[str, list[dict]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_INGEST_MAX_WORKERS, thread_name_prefix="roster") as executor:
        futures = {executor.submit(_fetch_one_roster, client, team_id): team_id for team_id in team_ids}
        for future in concurrent.futures.as_completed(futures):
            team_id = futures[future]
            try:
                injuries_by_team[team_id] = future.result()
                fetched += 1
            except Exception:
                logger.exception("Failed fetching roster for team %s", team_id)
                failed += 1
    return fetched, failed, injuries_by_team


def _attach_injuries(events: list[dict], injuries_by_team: dict[str, list[dict]]) -> None:
    """Attaches home_injuries/away_injuries onto each scoreboard event
    dict in place, from the same run's roster fetch above.
    library.normalize.espn.scoreboard_event_to_event_item already reads
    home_injuries/away_injuries off the event dict generically (shared
    across every sport), so no normalize-side change is needed once this
    is set.

    Forward-only: only events processed by an ingest run from here
    forward carry this field -- historical backfilled events never had a
    same-day roster fetch to attach.

    Best-effort: a team missing from injuries_by_team (its own roster
    fetch failed above) simply leaves that side's field unset rather than
    writing an empty list, preserving the "never checked" vs. "checked,
    nobody's hurt" distinction scoreboard_event_to_event_item's own
    docstring documents."""
    for evt in events:
        competitions = evt.get("competitions") or [{}]
        for competitor in competitions[0].get("competitors", []):
            team_id = str(competitor.get("team", {}).get("id", ""))
            role = competitor.get("homeAway")
            if role not in ("home", "away") or team_id not in injuries_by_team:
                continue
            evt[f"{role}_injuries"] = injuries_by_team[team_id]


def _fetch_current_ap_poll(client: NCAAMBBClient, core_client: NCAAMBBCoreClient) -> bool:
    """Fetches and writes this week's AP Top 25 poll to S3, under the
    exact same ncaambb/rankings/{season}/{season_type}/{week}.json key
    shape data-backfills/ncaambb/backfill.py's own seed_rankings uses --
    keeps the national-ranking model's training data current going
    forward, since backfill only ever covers polls up through whenever it
    last ran. Best-effort: no current poll (off-season, or ESPN hasn't
    released one yet) is a normal, logged no-op, not a failure -- this
    must never take down the rest of ingest over a genuinely secondary,
    non-critical data source."""
    pointer = current_ap_poll_pointer(client.get_current_rankings_pointer())
    if pointer is None:
        logger.info("No current AP poll pointer available -- skipping")
        return False
    season, season_type, week = pointer
    poll = core_client.get_ap_poll(season, season_type, week)
    if poll is None:
        logger.info("No AP poll found for season=%s type=%s week=%s -- skipping", season, season_type, week)
        return False
    _put_json(f"ncaambb/rankings/{season}/{season_type}/{week}.json", poll)
    return True


def lambda_handler(event: dict, context) -> dict:
    target_date = event.get("date") or _yesterday()
    client = NCAAMBBClient()

    # Written to S3 every run (uncached, like the roster fetch below) --
    # this is what normalize's team_to_entity call derives team display
    # name/abbreviation/color from (see library/normalize/espn.py). Also
    # the source of team ids for the roster fetch below, so one call
    # covers both rather than fetching /teams twice.
    teams_response = client.get_teams()
    _put_json("ncaambb/teams.json", teams_response)
    team_ids = _team_ids(teams_response)

    # Unconditional -- see this module's own docstring for why this runs
    # regardless of whether the target date has any games at all.
    rosters_fetched, rosters_failed, injuries_by_team = _fetch_rosters(client, team_ids)
    logger.info("Rosters: %d fetched, %d failed", rosters_fetched, rosters_failed)

    # Best-effort, isolated from the rest of this run's own error
    # handling -- see _fetch_current_ap_poll's own docstring for why a
    # ranking-fetch failure must never take down box-score/roster ingest.
    try:
        rankings_fetched = _fetch_current_ap_poll(client, NCAAMBBCoreClient())
    except Exception:
        logger.exception("Failed fetching current AP poll")
        rankings_fetched = False

    scoreboard = client.get_scoreboard_for_date(target_date)
    events = scoreboard.get("events", [])
    logger.info("Found %d events for date %s", len(events), target_date)
    # Mutates events in place -- events IS scoreboard["events"], so this
    # is reflected in the scoreboard payload written to S3 below.
    _attach_injuries(events, injuries_by_team)

    season = events[0]["season"]["year"] if events else None
    scoreboard_key = f"ncaambb/scoreboard/{target_date}.json"
    _put_json(scoreboard_key, scoreboard)

    # Filtered up front so the thread pool below is only ever sized to the
    # events actually worth an ESPN round-trip -- a quiet weeknight (a
    # handful of games) doesn't pay for _INGEST_MAX_WORKERS threads it has
    # no work for, and a full Saturday (~150 games, confirmed live -- see
    # this module's own VOLUME docstring section) is exactly the case the
    # pool exists for.
    to_fetch = []
    skipped = 0
    for evt in events:
        event_id = evt["id"]
        if not evt.get("status", {}).get("type", {}).get("completed", False):
            logger.debug("Skipping incomplete event %s", event_id)
            skipped += 1
            continue
        raw_key = f"ncaambb/boxscore/{season}/{event_id}.json"
        if _object_exists(raw_key):
            logger.debug("Box score already in S3, skipping event %s", event_id)
            skipped += 1
            continue
        to_fetch.append((event_id, raw_key))

    processed = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=_INGEST_MAX_WORKERS, thread_name_prefix="boxscore") as executor:
        futures = {executor.submit(client.get_summary, event_id): (event_id, raw_key) for event_id, raw_key in to_fetch}
        for future in concurrent.futures.as_completed(futures):
            event_id, raw_key = futures[future]
            try:
                _put_json(raw_key, future.result())
                processed += 1
            except Exception:
                logger.exception("Failed fetching summary for event %s", event_id)
                failed += 1

    logger.info("Done: %d processed, %d skipped, %d failed", processed, skipped, failed)
    return {
        "processed": processed, "skipped": skipped, "failed": failed,
        "rosters_fetched": rosters_fetched, "rosters_failed": rosters_failed,
        "rankings_fetched": rankings_fetched,
    }
