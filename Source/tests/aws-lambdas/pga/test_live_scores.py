"""
Unit tests for live-scores/live_scores.py -- the poll-window gate (which
tournaments are even worth an ESPN call this tick), the ESPN leaderboard
-> cached-state extraction, and the S3 cache's read/write/staleness
behavior. All AWS calls are mocked; live_scores.py takes
s3/bucket/storage/client as explicit arguments rather than holding its
own.

_in_tournament_range/_is_active/_candidate_events all take `day`/`now` as
explicit parameters (same pattern as schedule-sync's own
_in_refresh_window(start_date, today)), so those are tested directly with
fixed fixture dates -- no clock mocking needed. refresh() itself reads
datetime.now() internally (matching NBA/NFL's own live_scores.py), so
TestRefresh builds its fixtures relative to the real wall clock at test
time (same trick NBA's own test_live_scores.py uses) instead of freezing
the clock, which has no existing precedent in this codebase's tests.

live_scores is registered on sys.path by conftest.py.
"""
import json
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

import live_scores

BUCKET = "test-bucket"
SPORT = "pga"


def _make_s3():
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body.encode("utf-8") if isinstance(body, str) else body

    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    mock_s3._store = store
    return mock_s3


def _event(event_id: str, event_date: str, end_date: str | None = None, next_tee_time: str | None = None) -> dict:
    return {
        "event_id": event_id, "event_date": event_date, "end_date": end_date, "next_tee_time": next_tee_time,
        "status": "scheduled",
    }


def _competitor(athlete_id: str, status_name: str = "STATUS_FINISH", tee_time: str | None = None) -> dict:
    return {
        "id": athlete_id,
        "earnings": 0.0,
        "athlete": {"id": athlete_id, "displayName": f"Golfer {athlete_id}", "flag": {"alt": "USA"}, "amateur": False},
        "status": {
            "type": {"id": "2", "name": status_name, "state": "post", "completed": status_name == "STATUS_FINISH"},
            "position": {"id": "1", "displayName": "1", "isTie": False},
            **({"teeTime": tee_time} if tee_time else {}),
        },
        "score": {"value": 280.0, "displayValue": "E"},
        "linescores": [{"period": 1, "value": 70.0, "displayValue": "E"}],
    }


def _espn_event(event_id: str, *, competitors=None, tournament_name="BMW Championship", completed=False) -> dict:
    return {
        "id": event_id,
        "date": "2026-08-20T04:00Z",
        "endDate": "2026-08-23T04:00Z",
        "season": {"year": 2026},
        "seasonType": {"id": "2"},
        "purse": 20000000,
        "tournament": {"displayName": tournament_name, "major": False, "scoringSystem": {"name": "Medal"}},
        "status": {"type": {
            "name": "STATUS_FINAL" if completed else "STATUS_IN_PROGRESS",
            "state": "post" if completed else "in", "completed": completed,
        }},
        "courses": [{"id": "65", "name": "Bellerive Country Club", "host": True, "address": {"city": "St. Louis", "state": "MO"}}],
        "competitions": [{"competitors": competitors if competitors is not None else [_competitor("1")]}],
    }


class TestInTournamentRange:
    def test_false_when_day_is_before_event_date(self):
        event = _event("1", "2026-08-20", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 19)) is False

    def test_false_when_day_is_after_end_date(self):
        event = _event("1", "2026-08-20", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 24)) is False

    def test_true_within_the_range_inclusive(self):
        event = _event("1", "2026-08-20", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 20)) is True
        assert live_scores._in_tournament_range(event, date(2026, 8, 23)) is True

    def test_missing_end_date_falls_back_to_a_single_day_range(self):
        event = _event("1", "2026-08-20", None)
        assert live_scores._in_tournament_range(event, date(2026, 8, 20)) is True
        assert live_scores._in_tournament_range(event, date(2026, 8, 21)) is False

    def test_unparseable_event_date_returns_false(self):
        event = _event("1", "not-a-date", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 21)) is False


class TestIsActive:
    def test_true_when_any_participant_is_not_terminal(self):
        item = {"participants": [{"result": {"status": "scheduled"}}, {"result": {"status": "finished"}}]}
        assert live_scores._is_active(item) is True

    def test_false_when_every_participant_is_terminal(self):
        item = {"participants": [{"result": {"status": "finished"}}, {"result": {"status": "cut"}}]}
        assert live_scores._is_active(item) is False

    def test_withdrawn_and_mdf_count_as_terminal(self):
        item = {"participants": [{"result": {"status": "withdrawn"}}, {"result": {"status": "made_cut_did_not_finish"}}]}
        assert live_scores._is_active(item) is False

    def test_true_when_no_participants_at_all(self):
        assert live_scores._is_active({"participants": []}) is True


class TestCandidateEvents:
    def test_excludes_an_event_outside_its_tournament_range(self):
        now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, {})

        assert candidates == []

    def test_excludes_an_event_more_than_an_hour_before_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)  # 2h before a 13:00 tee time
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, {})

        assert candidates == []

    def test_includes_an_event_within_an_hour_of_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)  # 30min before a 13:00 tee time
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, {})

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_includes_an_event_past_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, {})

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_includes_an_event_with_no_known_tee_time_yet_within_range(self):
        # No next_tee_time published yet at all -- polled anyway to help
        # discover it, same "err toward still poll" precedent as
        # schedule-sync's own _in_refresh_window.
        now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", None)]

        candidates = live_scores._candidate_events(storage, SPORT, now, {})

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_includes_an_event_within_the_end_buffer_tail_even_before_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)  # outside the tee-time window
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", "2026-08-22T13:00Z")]
        last_active_at = {"1": (now - timedelta(minutes=30)).isoformat()}

        candidates = live_scores._candidate_events(storage, SPORT, now, last_active_at)

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_excludes_an_event_more_than_an_hour_past_last_active(self):
        now = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-08-20", "2026-08-23", "2026-08-22T13:00Z")]
        last_active_at = {"1": (now - timedelta(hours=2)).isoformat()}

        candidates = live_scores._candidate_events(storage, SPORT, now, last_active_at)

        assert candidates == []


class TestRefresh:
    """refresh() itself reads datetime.now() internally, so every fixture
    here is built relative to the real wall clock at test time (next tee
    time 30 minutes in the past, event_date/end_date = today) --
    guarantees "now" falls inside the computed poll window regardless of
    when the suite runs, without mocking the clock (no precedent for that
    in this codebase's tests -- see module docstring)."""

    def _current_tournament_event(self, event_id: str) -> dict:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        next_tee_time = (now - timedelta(minutes=30)).isoformat()
        return _event(event_id, today, today, next_tee_time)

    def test_no_candidates_never_calls_espn(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        client = MagicMock()
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}
        client.get_leaderboard.assert_not_called()
        s3.put_object.assert_not_called()

    def test_a_candidate_gets_fetched_and_cached(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_tournament_event("1")]
        client = MagicMock()
        client.get_leaderboard.return_value = {"events": [_espn_event("1", competitors=[_competitor("10")])]}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 1}
        client.get_leaderboard.assert_called_once_with("1")
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["tournament_name"] == "BMW Championship"
        assert cached["events"]["1"]["participants"]["10"]["status"] == "finished"

    def test_a_non_stroke_play_candidate_is_skipped_not_errored(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_tournament_event("1")]
        client = MagicMock()
        match_event = _espn_event("1")
        match_event["tournament"]["scoringSystem"]["name"] = "Match"
        client.get_leaderboard.return_value = {"events": [match_event]}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}

    def test_an_empty_leaderboard_response_is_skipped_not_errored(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_tournament_event("1")]
        client = MagicMock()
        client.get_leaderboard.return_value = {"events": []}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}

    def test_one_candidates_fetch_failure_does_not_kill_the_others(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            self._current_tournament_event("1"), self._current_tournament_event("2"),
        ]
        client = MagicMock()

        def _get_leaderboard(event_id):
            if event_id == "1":
                raise RuntimeError("ESPN hiccup")
            return {"events": [_espn_event("2", competitors=[_competitor("20")])]}

        client.get_leaderboard.side_effect = _get_leaderboard
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 1}
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert "1" not in cached["events"]
        assert "2" in cached["events"]

    def test_a_still_active_event_records_last_active_at_for_the_end_buffer_tail(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_tournament_event("1")]
        client = MagicMock()
        client.get_leaderboard.return_value = {
            "events": [_espn_event("1", competitors=[_competitor("10", "STATUS_SCHEDULED")])],
        }
        s3 = _make_s3()

        live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert "last_active_at" in cached["events"]["1"]

    def test_a_now_finished_event_preserves_its_prior_last_active_at(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_tournament_event("1")]
        client = MagicMock()
        client.get_leaderboard.return_value = {
            "events": [_espn_event("1", competitors=[_competitor("10", "STATUS_FINISH")], completed=True)],
        }
        s3 = _make_s3()
        prior_active_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": prior_active_at,
            "events": {"1": {"status": "scheduled", "tournament_name": "BMW Championship", "participants": {}, "last_active_at": prior_active_at}},
        }).encode("utf-8")

        live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["last_active_at"] == prior_active_at


class TestGetLiveScores:
    def test_returns_empty_when_nothing_cached_yet(self):
        s3 = _make_s3()

        assert live_scores.get_live_scores(s3, BUCKET) == {"events": {}}

    def test_returns_cached_events_when_fresh(self):
        s3 = _make_s3()
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "events": {"1": {"status": "scheduled", "tournament_name": "BMW Championship", "participants": {}}},
        }).encode("utf-8")

        result = live_scores.get_live_scores(s3, BUCKET)

        assert result["events"]["1"]["tournament_name"] == "BMW Championship"

    def test_returns_empty_when_the_cache_is_stale(self):
        s3 = _make_s3()
        stale_fetch = datetime.now(timezone.utc) - live_scores.STALE_AFTER - timedelta(minutes=1)
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": stale_fetch.isoformat(),
            "events": {"1": {"status": "scheduled", "tournament_name": "BMW Championship", "participants": {}}},
        }).encode("utf-8")

        result = live_scores.get_live_scores(s3, BUCKET)

        assert result == {"events": {}}
