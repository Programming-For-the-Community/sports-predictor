"""Games ESPN drops from a past date's scoreboard are canceled; nothing else is touched."""
import logging
from unittest.mock import patch

from library.normalize import unlisted_events

LOGGER = logging.getLogger("test")


class FakeStorage:
    def __init__(self, scheduled):
        self.scheduled = scheduled
        self.asked = None
        self.upserted = []

    def get_events_on_date(self, sport, status, event_date):
        self.asked = (sport, status, event_date)
        return [e for e in self.scheduled if e["event_date"] == event_date]

    def upsert_event(self, item):
        self.upserted.append(item)


def _run(storage, key, listed_ids, today="2026-11-08"):
    payload = {"events": [{"id": i} for i in listed_ids]}
    with patch.object(unlisted_events, "us_eastern_date", return_value=today):
        return unlisted_events.cancel_unlisted(storage, "ncaambb", key, payload, LOGGER)


def test_a_past_date_s_unlisted_scheduled_game_is_canceled():
    storage = FakeStorage([
        {"event_id": "401931195", "event_date": "2026-11-07", "status": "scheduled"},
        {"event_id": "401925923", "event_date": "2026-11-07", "status": "scheduled"},
    ])

    canceled = _run(storage, "ncaambb/scoreboard/20261107.json", ["401931195", "401931055"])

    assert canceled == 1
    assert storage.asked == ("ncaambb", "scheduled", "2026-11-07")
    assert storage.upserted == [{"event_id": "401925923", "event_date": "2026-11-07", "status": "canceled"}]


def test_today_future_empty_and_week_keyed_scoreboards_cancel_nothing():
    storage = FakeStorage([{"event_id": "9", "event_date": "2026-11-08", "status": "scheduled"}])

    assert _run(storage, "ncaambb/scoreboard/20261108.json", ["1"]) == 0          # today
    assert _run(storage, "ncaambb/scoreboard/20261109.json", ["1"]) == 0          # future
    assert _run(storage, "ncaambb/scoreboard/20261107.json", []) == 0             # empty fetch
    assert _run(storage, "nfl/scoreboard/2026/2/9.json", ["1"]) == 0              # week-keyed
    assert storage.upserted == []


def test_scoreboard_date_reads_the_key():
    assert unlisted_events.scoreboard_date("nba/scoreboard/20261107.json") == "2026-11-07"
    assert unlisted_events.scoreboard_date("nfl/scoreboard/2026/2/9.json") is None
