"""
Decides, once per tick, which upcoming head-to-head events need a
pre-kickoff refresh and/or snapshot, and fires them. The shared
prediction-scheduler Lambda (aws-lambdas/shared/prediction-scheduler)
calls run_tick every 5 minutes.

Per event, with T = kickoff:

    T-30min .. T-15min   REFRESH: re-run the sport's own ingest for that
                         event's week/date with force_refresh, so injuries,
                         rosters and depth charts on the event item are
                         current (ingest -> S3 -> normalize -> event item).
                         Skipped for a sport with no such data (NCAAFB).
    T-15min .. T         SNAPSHOT: invoke the sport's predict Lambda with
                         SnapshotPrediction -- one fresh compute, copied to
                         the event's immutable final-pregame snapshot
                         (library.serving.prediction_snapshots).

The refresh runs in the ingest Lambda (internet access, owns the enrichment
code) rather than here or in the predict Lambda, which sits in the VPC.

Claims: each step first writes a SNAPSHOT_STATE# marker row to the
predictions table so overlapping/repeat ticks don't fire it twice. A snapshot
claim expires after RETRY_AFTER so a failed attempt is retried on a later
tick, still before kickoff; a refresh is attempted once.

A sport with `snapshot_delay` runs the same two steps that long after
kickoff instead of before it. NHL uses it: the starting goalies are only
confirmed once the game starts, so its refresh lands at T .. T+15min and
its snapshot at T+15min .. T+30min.

PGA and F1 have no kickoff to count down to, and are graded against ONE
snapshot taken at the start of the event. A sport with
`event_start_snapshot_utc_hour` gets a single SnapshotPrediction in a short
window around the event's own date, at the first tick after that hour (after
the daily ingest and normalize have landed the latest results -- for F1 that
includes qualifying). PGA gets a second chance the day before (Wednesday);
F1 snapshots on race day only, since the grid is not known until qualifying.
The event's snapshot existing is what ends the attempts.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from boto3.dynamodb.conditions import Attr, Key

from library.parsing import us_eastern_date
from library.serving import prediction_snapshots as snapshots

REFRESH_LEAD = timedelta(minutes=30)
SNAPSHOT_LEAD = timedelta(minutes=15)
RETRY_AFTER = timedelta(minutes=7)

REFRESH_MARKER = "SNAPSHOT_STATE#refresh"
SNAPSHOT_MARKER = "SNAPSHOT_STATE#snapshot"
START_MARKER = "SNAPSHOT_STATE#event_start"


def _nfl_ingest_payload(event: dict) -> dict:
    return {"season": event["season"], "season_type": event["season_type"], "week": event["week"], "force_refresh": True}


def _date_ingest_payload(event: dict) -> dict:
    # basketball ingests are keyed by scoreboard date (YYYYMMDD)
    return {"date": event["event_date"].replace("-", ""), "force_refresh": True}


@dataclass(frozen=True)
class SportSchedule:
    # Builds the ingest payload that refreshes this event's context; None when
    # the sport has no injury/depth-chart data to refresh.
    ingest_payload: Callable[[dict], dict] | None
    # PGA/F1-style: one snapshot at the start of the event, from this UTC hour
    # on, instead of near a kickoff.
    event_start_snapshot_utc_hour: int | None = None
    # ... starting this many days before the event's own date (a first attempt
    # and a retry), and only for these event types.
    days_before_start: int = 0
    event_types: frozenset[str] = frozenset()
    # Shifts the refresh and snapshot windows this long past kickoff.
    snapshot_delay: timedelta = timedelta(0)


SPORT_SCHEDULES = {
    "nfl": SportSchedule(_nfl_ingest_payload),
    "ncaafb": SportSchedule(None),
    "nba": SportSchedule(_date_ingest_payload),
    "ncaambb": SportSchedule(_date_ingest_payload),
    "pga": SportSchedule(None, event_start_snapshot_utc_hour=11, days_before_start=1, event_types=frozenset({"field"})),
    "f1": SportSchedule(None, event_start_snapshot_utc_hour=11, event_types=frozenset({"field", "sprint"})),
    "nhl": SportSchedule(_date_ingest_payload, snapshot_delay=REFRESH_LEAD),
}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _claim(predictions_table, event_key: str, marker: str, now: datetime, retry_after: timedelta | None) -> bool:
    """True if this tick now owns `marker` for the event."""
    item = {"event_key": event_key, "model_key": marker, "generated_at": now.isoformat()}
    if predictions_table.put_item(item, condition_expression=Attr("model_key").not_exists()):
        return True
    if retry_after is None:
        return False
    existing = predictions_table.get_item({"event_key": event_key, "model_key": marker})
    if existing is not None and now - _parse_time(existing["generated_at"]) < retry_after:
        return False
    predictions_table.put_item(item)
    return True


def upcoming_events(events_table, sport: str, now: datetime) -> list[dict]:
    """Scheduled events dated yesterday..tomorrow (US Eastern, the calendar
    event_date uses) -- wide enough to cover any kickoff in the next hour
    without reading a sport's whole schedule (NCAAMBB has thousands)."""
    start, end = (us_eastern_date(now + timedelta(days=offset)) for offset in (-1, 1))
    return events_table.query(
        Key("sport_status").eq(f"{sport}#scheduled") & Key("event_date").between(start, end),
        index_name="sport-status-index",
    )


def _event_start_snapshot_due(event: dict, now: datetime, schedule: SportSchedule, predictions_table) -> bool:
    """True (and the attempt claimed) when the event is of a graded type, its
    start-of-event window is open, it is past the snapshot hour, and it has no
    snapshot yet."""
    if event.get("event_type") not in schedule.event_types or now.hour < schedule.event_start_snapshot_utc_hour:
        return False
    start = date.fromisoformat(event["event_date"])
    if not start - timedelta(days=schedule.days_before_start) <= now.date() <= start:
        return False
    if snapshots.has_snapshot(predictions_table, event["event_key"]):
        return False
    return _claim(predictions_table, event["event_key"], START_MARKER, now, RETRY_AFTER)


_SNAPSHOT = "snapshot"
_REFRESH = "refresh"


def _kickoff_action(event: dict, now: datetime, schedule: SportSchedule, predictions_table) -> str | None:
    """Snapshot inside SNAPSHOT_LEAD of kickoff (plus the sport's
    snapshot_delay), refresh inside REFRESH_LEAD, each only once its
    marker is claimed."""
    kickoff_raw = event.get("kickoff_time")
    if not kickoff_raw:
        return None
    until = _parse_time(kickoff_raw) + schedule.snapshot_delay - now
    if until <= timedelta(0):
        return None
    if until <= SNAPSHOT_LEAD:
        if snapshots.has_snapshot(predictions_table, event["event_key"]):
            return None
        return _SNAPSHOT if _claim(predictions_table, event["event_key"], SNAPSHOT_MARKER, now, RETRY_AFTER) else None
    if (
        until <= REFRESH_LEAD
        and schedule.ingest_payload is not None
        and _claim(predictions_table, event["event_key"], REFRESH_MARKER, now, None)
    ):
        return _REFRESH
    return None


def _event_action(event: dict, now: datetime, schedule: SportSchedule, predictions_table) -> str | None:
    if schedule.event_start_snapshot_utc_hour is not None:
        return _SNAPSHOT if _event_start_snapshot_due(event, now, schedule, predictions_table) else None
    return _kickoff_action(event, now, schedule, predictions_table)


def run_tick(
    now: datetime, sports: dict[str, SportSchedule], list_events: Callable[[str], list[dict]], predictions_table,
    invoke: Callable[[str, dict], None], project: str,
) -> dict:
    """One scheduler pass. `invoke(function_name, payload)` fires a Lambda
    asynchronously. Returns counts per sport for logging."""
    summary: dict[str, dict[str, int]] = {}
    for sport, schedule in sports.items():
        refresh_payloads: dict[tuple, dict] = {}
        snapshot_events: list[dict] = []
        for event in list_events(sport):
            action = _event_action(event, now, schedule, predictions_table)
            if action == _SNAPSHOT:
                snapshot_events.append(event)
            elif action == _REFRESH:
                payload = schedule.ingest_payload(event)
                refresh_payloads[tuple(sorted(payload.items()))] = payload

        for payload in refresh_payloads.values():
            invoke(f"{project}-{sport}-ingest", payload)
        for event in snapshot_events:
            invoke(f"{project}-{sport}-predict", {"detail-type": "SnapshotPrediction", "event_id": event["event_id"]})
        summary[sport] = {"refreshes": len(refresh_payloads), "snapshots": len(snapshot_events)}
    return summary
