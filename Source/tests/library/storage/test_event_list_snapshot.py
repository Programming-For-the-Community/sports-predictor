"""
Unit tests for library.storage.event_list_snapshot -- the reusable built
completed-events list. s3 is a MagicMock throughout.
"""
import time
from unittest.mock import MagicMock

from library.storage import event_list_snapshot

EVENTS = [{"event_key": "SPORT#NBA#EVENT#e1", "participants": [{"entity_id": "13", "result": {"score": 101}}]}]


def _s3(snapshot: dict | None):
    s3 = MagicMock()
    s3.get_json_or_none.return_value = snapshot
    return s3


class TestFingerprint:
    def test_stable_for_the_same_events(self):
        assert event_list_snapshot.fingerprint(EVENTS) == event_list_snapshot.fingerprint([dict(EVENTS[0])])

    def test_changes_when_an_event_changes(self):
        changed = [{**EVENTS[0], "participants": [{"entity_id": "13", "result": {"score": 102}}]}]

        assert event_list_snapshot.fingerprint(changed) != event_list_snapshot.fingerprint(EVENTS)

    def test_changes_when_the_set_of_events_changes(self):
        assert event_list_snapshot.fingerprint(EVENTS + [{"event_key": "e2"}]) != event_list_snapshot.fingerprint(EVENTS)


class TestRead:
    def test_none_without_a_snapshot(self):
        assert event_list_snapshot.read(_s3(None), "nba", "abc") is None

    def test_returns_entries_for_a_matching_recent_snapshot(self):
        s3 = _s3({"fingerprint": "abc", "built_at_epoch": time.time(), "entries": [{"event_id": "e1"}]})

        assert event_list_snapshot.read(s3, "nba", "abc") == [{"event_id": "e1"}]
        s3.get_json_or_none.assert_called_once_with("predictions-cache/lists/nba/completed.json")

    def test_none_when_built_from_different_events(self):
        s3 = _s3({"fingerprint": "old", "built_at_epoch": time.time(), "entries": []})

        assert event_list_snapshot.read(s3, "nba", "abc") is None

    def test_none_once_aged_out(self):
        built_at = time.time() - event_list_snapshot.MAX_AGE_SECONDS - 1
        s3 = _s3({"fingerprint": "abc", "built_at_epoch": built_at, "entries": []})

        assert event_list_snapshot.read(s3, "nba", "abc") is None


class TestWrite:
    def test_writes_fingerprint_and_entries(self):
        s3 = MagicMock()

        event_list_snapshot.write(s3, "nba", "abc", [{"event_id": "e1"}])

        key, payload = s3.put_json.call_args.args
        assert key == "predictions-cache/lists/nba/completed.json"
        assert payload["fingerprint"] == "abc"
        assert payload["entries"] == [{"event_id": "e1"}]

    def test_a_failed_write_does_not_raise(self):
        s3 = MagicMock()
        s3.put_json.side_effect = RuntimeError("s3 down")

        event_list_snapshot.write(s3, "nba", "abc", [])
