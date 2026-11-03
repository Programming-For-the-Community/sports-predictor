"""
S3 snapshot of a sport's whole completed-event history, the input every live
prediction's Elo ratings and team form are computed from. Reading it from
DynamoDB costs one 1MB page per ~2,000 events (NCAA MBB: ~30 pages); a
snapshot hit costs one small Query and one S3 read.

A snapshot is valid only while the last PROBE_DAYS of completed events are
exactly the ones it was built with -- that window is where new results and
score corrections land -- and for at most MAX_AGE_SECONDS, which bounds an
older correction.
"""
import gzip
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from botocore.exceptions import ClientError

from library.parsing import us_eastern_date
from library.storage import event_list_snapshot

logger = logging.getLogger(__name__)

PROBE_DAYS = 7
MAX_AGE_SECONDS = 6 * 60 * 60


def snapshot_key(sport: str) -> str:
    return f"predictions-cache/history/{sport}/completed-events.json.gz"


def _recent_fingerprint(events: list[dict], since_date: str) -> str:
    recent = sorted((e for e in events if e.get("event_date", "") >= since_date), key=lambda e: e["event_key"])
    return event_list_snapshot.fingerprint(recent)


def _read(s3, sport: str, fingerprint: str) -> list[dict] | None:
    try:
        snapshot = json.loads(gzip.decompress(s3.get_bytes(snapshot_key(sport))))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            logger.warning("Failed to read the %s completed-events snapshot", sport, exc_info=True)
        return None
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read the %s completed-events snapshot", sport, exc_info=True)
        return None
    if snapshot.get("fingerprint") != fingerprint or time.time() - snapshot.get("built_at_epoch", 0) >= MAX_AGE_SECONDS:
        return None
    return snapshot["events"]


def _write(s3, sport: str, fingerprint: str, events: list[dict]) -> None:
    """Never raises -- a failed write only costs the next request a full read."""
    try:
        body = json.dumps({"fingerprint": fingerprint, "built_at_epoch": time.time(), "events": events})
        data = gzip.compress(body.encode("utf-8"), compresslevel=1)
        s3.put_bytes(snapshot_key(sport), data, content_type="application/gzip")
    except Exception:  # noqa: BLE001
        logger.warning("Failed to write the %s completed-events snapshot", sport, exc_info=True)


def completed_events(storage, s3, sport: str, now: datetime | None = None) -> list[dict]:
    """storage.get_all_events(sport), served from the snapshot when it is
    still current."""
    today = date.fromisoformat(us_eastern_date(now or datetime.now(timezone.utc)))
    since_date = (today - timedelta(days=PROBE_DAYS)).isoformat()
    probe = _recent_fingerprint(storage.get_all_events(sport, since_date=since_date), since_date)
    cached = _read(s3, sport, probe)
    if cached is not None:
        return cached
    events = storage.get_all_events(sport)
    # Fingerprinted from the full read itself, so a result landing between
    # the probe and this read can't be paired with a snapshot that lacks it.
    _write(s3, sport, _recent_fingerprint(events, since_date), events)
    return events
