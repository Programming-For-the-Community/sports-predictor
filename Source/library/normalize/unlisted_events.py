"""
Cancels games ESPN has dropped from a past date's scoreboard. ESPN sometimes
lists a game under a second id and later deletes one of them; the deleted one
never reaches a final score, so without this it stays "scheduled" forever.
A date-keyed scoreboard is the whole slate for its US Eastern date (the
clients ask for every game), so a game still scheduled on a past date that
the scoreboard no longer lists is gone. A later scoreboard listing it again
sets its status back.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from library.parsing import us_eastern_date

# ".../scoreboard/20261107.json": the scoreboard's ESPN (US Eastern) date.
_DATE_KEY = re.compile(r"/scoreboard/(\d{4})(\d{2})(\d{2})\.json$")


def scoreboard_date(key: str) -> str | None:
    match = _DATE_KEY.search(key)
    return "-".join(match.groups()) if match else None


def cancel_unlisted(storage, sport: str, key: str, payload: dict, logger: logging.Logger) -> int:
    """Marks this date's still-scheduled games that the scoreboard doesn't
    list as canceled; returns how many. Only past dates, and never on an
    empty scoreboard (a failed or empty fetch says nothing about a game)."""
    event_date = scoreboard_date(key)
    listed = {str(event.get("id")) for event in payload.get("events") or []}
    if event_date is None or not listed or event_date >= us_eastern_date(datetime.now(timezone.utc)):
        return 0
    gone = [e for e in storage.get_events_on_date(sport, "scheduled", event_date) if str(e.get("event_id")) not in listed]
    for event in gone:
        storage.upsert_event({**event, "status": "canceled"})
        logger.warning("Canceled %s event %s on %s: no longer on ESPN's scoreboard", sport, event.get("event_id"), event_date)
    return len(gone)
