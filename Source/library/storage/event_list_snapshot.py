"""
S3 snapshot of a sport's built completed-events list (GET /{sport}/events?
status=completed), reused for as long as the events it was built from are
unchanged. Building that list costs a predictions query, a player-stats
query and several entity reads per event; a snapshot hit skips all of it.

A snapshot is valid only for the exact event rows it was built from (their
fingerprint), and for at most MAX_AGE_SECONDS -- player stat lines can land
or be corrected after an event's own row last changed.
"""
import hashlib
import json
import logging
import time

logger = logging.getLogger(__name__)

MAX_AGE_SECONDS = 60 * 60


def snapshot_key(sport: str) -> str:
    return f"predictions-cache/lists/{sport}/completed.json"


def fingerprint(events: list[dict]) -> str:
    """Changes whenever any of `events` does, or the set itself does."""
    canonical = json.dumps(events, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read(s3, sport: str, events_fingerprint: str) -> list[dict] | None:
    """The entries built for exactly these events, None if there's no
    snapshot, it was built from different events, or it has aged out."""
    snapshot = s3.get_json_or_none(snapshot_key(sport))
    if snapshot is None or snapshot.get("fingerprint") != events_fingerprint:
        return None
    if time.time() - snapshot.get("built_at_epoch", 0) >= MAX_AGE_SECONDS:
        return None
    return snapshot.get("entries")


def write(s3, sport: str, events_fingerprint: str, entries: list[dict]) -> None:
    """Never raises -- a failed write only costs the next request a rebuild."""
    try:
        s3.put_json(snapshot_key(sport), {
            "fingerprint": events_fingerprint,
            "built_at_epoch": time.time(),
            "entries": entries,
        })
    except Exception:
        logger.warning("Failed to write the %s completed-events snapshot", sport, exc_info=True)
