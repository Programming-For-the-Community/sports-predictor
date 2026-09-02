"""
F1 ingest Lambda. Triggered daily by the shared ingest-orchestrator Step
Function (Terraform/sfn-ingest-orchestrator.tf), which invokes every
active sport's own "${project}-<sport>-ingest" Lambda by naming
convention -- see design/DATA_SCHEMA.md's sport registry section: neither
orchestrator branches on event_type, so F1 needs no orchestration change
to onboard through this same path (same precedent as PGA's own onboarding).

Genuinely different discovery shape from every other sport's ingest:
Jolpica has no "what's current for this date" scoreboard-style endpoint
(library/http/f1.py's own docstring) -- the closest analog is the
season's own race calendar (JolpicaClient.get_races), refetched every
run and walked to find which round(s) fall in a short trailing window
around today's date (a race that just finished; results can lag a day
or two behind the calendar date). For each candidate round, fetches and
writes raw results/qualifying/sprint/pitstops JSON to S3 -- normalize
(triggered by the results PutObject) does the rest. Qualifying/sprint/
pitstops are written straight to their own raw prefixes and read
directly from S3 by feature engineering, never routed through normalize/
DynamoDB -- same "not every raw payload needs its own DynamoDB item"
precedent PGA's own season-stats snapshot already established.

Also captures driver/constructor championship standings as of the most
recently completed round found this run, unconditionally whenever a
round was actually processed -- needed by season simulation (current
standings context). Only captured when a round was actually processed
(unlike PGA's own stats snapshot, which runs every day regardless):
standings are only meaningfully different right after a round completes,
so an off week between races has nothing new to snapshot.

Also writes the season's own full calendar (the SAME `schedule` response
already fetched above to find this run's trailing-window candidates) to
its own raw prefix every run, unconditionally -- normalize turns this
into a "scheduled" stub event per remaining race (library/normalize/f1.py's
schedule_payload_to_scheduled_events), the only way season simulation
(aws-lambdas/f1/predict/season_projection.py) can learn each remaining
race's own circuit_id/event_date at all, since Jolpica has no separate
"what's upcoming" scoreboard endpoint. Zero extra Jolpica requests --
`schedule` is already in hand.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta

import boto3

from library.http.f1 import JolpicaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", force=True)  # AWS Lambda pre-attaches a root handler, so basicConfig() is otherwise a silent no-op
logger = logging.getLogger("f1-ingest")

RAW_BUCKET = os.environ["RAW_BUCKET_NAME"]
# 2011+ only -- Jolpica's own pitstops endpoint has no earlier data
# (library/http/f1.py's own docstring).
PITSTOPS_MIN_SEASON = 2011
# How many days back from target_date still counts as "recently
# finished" -- covers a Sunday race whose results this Monday/Tuesday's
# run should still pick up, without re-walking the whole season calendar
# for a fetch every day.
TRAILING_WINDOW_DAYS = 3

_s3 = boto3.client("s3")


def _put_json(key: str, payload) -> None:
    _s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=json.dumps(payload).encode("utf-8"), ContentType="application/json")
    logger.info("Wrote s3://%s/%s", RAW_BUCKET, key)


def _races_in_window(schedule: dict, target_date: date) -> list[dict]:
    races = schedule.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    window_start = target_date - timedelta(days=TRAILING_WINDOW_DAYS)
    in_window = []
    for race in races:
        race_date_str = race.get("date")
        if not race_date_str:
            continue
        race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
        if window_start <= race_date <= target_date:
            in_window.append(race)
    return in_window


def _fetch_and_write_round(client: JolpicaClient, season: int, round_: int) -> None:
    results = client.get_race_results(season, round_)
    _put_json(f"f1/results/{season}/{round_}.json", results)

    qualifying = client.get_qualifying(season, round_)
    _put_json(f"f1/qualifying/{season}/{round_}.json", qualifying)

    # Only written when this round actually was a Sprint weekend --
    # get_sprint returns an empty Races list for a normal weekend, and
    # writing an empty file every round would just be noise.
    sprint = client.get_sprint(season, round_)
    sprint_races = sprint.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if sprint_races and sprint_races[0].get("SprintResults"):
        _put_json(f"f1/sprint/{season}/{round_}.json", sprint)

    if season >= PITSTOPS_MIN_SEASON:
        pitstops = client.get_pitstops(season, round_)
        _put_json(f"f1/pitstops/{season}/{round_}.json", pitstops)


def _fetch_standings_snapshot(client: JolpicaClient, season: int, round_: int, target_date: str) -> bool:
    """Best-effort -- a failed standings fetch shouldn't block the real
    per-round results ingest above."""
    try:
        driver_standings = client.get_driver_standings(season, round_)
        constructor_standings = client.get_constructor_standings(season, round_)
        _put_json(f"f1/standings/{season}/{target_date}.json", {
            "driver_standings": driver_standings,
            "constructor_standings": constructor_standings,
            "as_of_round": round_,
        })
        return True
    except Exception:
        logger.exception("Failed fetching standings snapshot for season %d as of round %d", season, round_)
        return False


def lambda_handler(event: dict, context) -> dict:
    target_date_str = event.get("date")
    target_date = datetime.strptime(target_date_str, "%Y%m%d").date() if target_date_str else date.today()
    season = event.get("season") or target_date.year

    client = JolpicaClient()
    schedule = client.get_races(season)
    _put_json(f"f1/schedule/{season}/{target_date.strftime('%Y%m%d')}.json", schedule)

    candidates = _races_in_window(schedule, target_date)
    logger.info("Found %d race(s) in the trailing window for season %d, date %s", len(candidates), season, target_date)

    processed = failed = 0
    latest_round = None
    for race in candidates:
        round_ = int(race["round"])
        try:
            _fetch_and_write_round(client, season, round_)
            processed += 1
            latest_round = round_ if latest_round is None else max(latest_round, round_)
        except Exception:
            logger.exception("Failed fetching round %d of season %d", round_, season)
            failed += 1

    standings_captured = False
    if latest_round is not None:
        standings_captured = _fetch_standings_snapshot(client, season, latest_round, target_date.strftime("%Y%m%d"))

    logger.info("Done: %d processed, %d failed, standings_captured=%s", processed, failed, standings_captured)
    return {"processed": processed, "failed": failed, "standings_captured": standings_captured}
