"""
Unit tests for library.storage.completed_events_snapshot -- an in-memory
fake stands in for the model bucket, storage is a MagicMock.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from library.storage import completed_events_snapshot

NOW = datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)
OLD = {"event_key": "E1", "event_date": "2025-03-01", "status": "completed"}
RECENT = {"event_key": "E2", "event_date": "2026-11-01", "status": "completed"}


class _FakeBucket:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.puts = 0

    def get_bytes(self, key):
        if key not in self.store:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return self.store[key]

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.puts += 1
        self.store[key] = data


def _storage(history):
    """get_all_events: the probe (since_date given) sees only the recent window."""
    storage = MagicMock()
    storage.get_all_events.side_effect = lambda sport, since_date=None: [
        e for e in history if since_date is None or e["event_date"] >= since_date
    ]
    return storage


def _full_reads(storage):
    return [c for c in storage.get_all_events.call_args_list if c.kwargs.get("since_date") is None]


class TestCompletedEvents:
    def test_a_miss_reads_dynamodb_and_writes_the_snapshot(self):
        storage, bucket = _storage([RECENT, OLD]), _FakeBucket()

        events = completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW)

        assert events == [RECENT, OLD]
        assert len(_full_reads(storage)) == 1
        assert bucket.puts == 1

    def test_a_current_snapshot_skips_the_full_read(self):
        bucket = _FakeBucket()
        completed_events_snapshot.completed_events(_storage([RECENT, OLD]), bucket, "ncaambb", NOW)
        storage = _storage([RECENT, OLD])

        events = completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW)

        assert events == [RECENT, OLD]
        assert _full_reads(storage) == []
        storage.get_all_events.assert_called_once_with("ncaambb", since_date="2026-10-26")

    def test_a_new_recent_result_rebuilds(self):
        bucket = _FakeBucket()
        completed_events_snapshot.completed_events(_storage([RECENT, OLD]), bucket, "ncaambb", NOW)
        newer = {"event_key": "E3", "event_date": "2026-11-02", "status": "completed"}
        storage = _storage([newer, RECENT, OLD])

        events = completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW)

        assert events == [newer, RECENT, OLD]
        assert len(_full_reads(storage)) == 1

    def test_a_corrected_recent_score_rebuilds(self):
        bucket = _FakeBucket()
        completed_events_snapshot.completed_events(_storage([RECENT, OLD]), bucket, "ncaambb", NOW)
        storage = _storage([{**RECENT, "home_score": 70}, OLD])

        completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW)

        assert len(_full_reads(storage)) == 1

    def test_an_aged_out_snapshot_rebuilds(self):
        bucket = _FakeBucket()
        with patch.object(completed_events_snapshot.time, "time", return_value=0):
            completed_events_snapshot.completed_events(_storage([RECENT, OLD]), bucket, "ncaambb", NOW)
        storage = _storage([RECENT, OLD])

        with patch.object(completed_events_snapshot.time, "time", return_value=completed_events_snapshot.MAX_AGE_SECONDS):
            completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW)

        assert len(_full_reads(storage)) == 1

    def test_an_unreadable_bucket_falls_back_to_dynamodb(self):
        storage, bucket = _storage([RECENT, OLD]), MagicMock()
        bucket.get_bytes.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
        bucket.put_bytes.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

        assert completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW) == [RECENT, OLD]

    def test_the_probe_window_is_order_independent(self):
        bucket = _FakeBucket()
        other = {"event_key": "E4", "event_date": "2026-11-01", "status": "completed"}
        completed_events_snapshot.completed_events(_storage([RECENT, other, OLD]), bucket, "ncaambb", NOW)
        storage = _storage([other, RECENT, OLD])

        completed_events_snapshot.completed_events(storage, bucket, "ncaambb", NOW)

        assert _full_reads(storage) == []
