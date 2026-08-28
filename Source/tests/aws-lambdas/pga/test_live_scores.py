"""
Unit tests for live-scores/live_scores.py -- the poll-window gate for both
field events (which tournaments are worth an ESPN call this tick) and
match_play/cup events (which real tournament ids are worth a call, deduped
across their many synthesized match rows), the ESPN leaderboard ->
cached-state extraction for all three event_types, and the S3 cache's
read/write/staleness behavior. All AWS calls are mocked; live_scores.py
takes s3/bucket/storage/client as explicit arguments rather than holding
its own.

_in_tournament_range/_is_active/_field_candidates/_match_cup_tournament_ids
all take `day`/`now` as explicit parameters (same pattern as schedule-
sync's own _in_refresh_window(start_date, today)), so those are tested
directly with fixed fixture dates -- no clock mocking needed. refresh()
itself reads datetime.now() internally (matching NBA/NFL's own
live_scores.py), so TestRefresh builds its fixtures relative to the real
wall clock at test time (same trick NBA's own test_live_scores.py uses)
instead of freezing the clock, which has no existing precedent in this
codebase's tests.

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


def _field_event(event_id: str, event_date: str, end_date: str | None = None, next_tee_time: str | None = None) -> dict:
    return {
        "event_id": event_id, "event_type": "field", "event_date": event_date, "end_date": end_date,
        "next_tee_time": next_tee_time, "status": "scheduled",
    }


def _match_row(
    event_id: str, parent_event_id: str, event_date: str, end_date: str | None = None, match_time: str | None = None,
) -> dict:
    return {
        "event_id": event_id, "event_type": "match_play", "parent_event_id": parent_event_id,
        "event_date": event_date, "end_date": end_date, "match_time": match_time, "status": "scheduled",
    }


def _cup_row(event_id: str, event_date: str, end_date: str | None = None) -> dict:
    return {"event_id": event_id, "event_type": "cup", "event_date": event_date, "end_date": end_date, "status": "scheduled"}


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


def _match_competitor(home_away, team_id, team_display, golfer_ids_names, won=False, halved=False, margin="", value=0.0):
    return {
        "id": golfer_ids_names[0][0],
        "homeAway": home_away,
        "status": {"type": {"id": "2", "name": "STATUS_FINISH", "state": "post", "completed": True}},
        "score": {"value": value, "displayValue": margin, "draw": halved, "winner": won},
        "team": {"id": team_id, "abbreviation": team_display, "displayName": team_display},
        "roster": [
            {"playerId": gid, "athlete": {"id": gid, "displayName": name, "flag": {"alt": "USA"}, "amateur": False}}
            for gid, name in golfer_ids_names
        ],
    }


def _cup_summary_entry(home_points=17.5, away_points=12.5):
    return {
        "id": "10950", "description": "tournament", "type": {"id": "1", "text": "tournament"},
        "scoringSystem": {"id": "4", "name": "Cup"},
        "competitors": [
            {"id": "1", "homeAway": "home", "score": {"value": home_points, "displayValue": str(home_points), "winner": home_points > away_points}, "team": {"id": "1", "abbreviation": "USA", "displayName": "USA"}},
            {"id": "3", "homeAway": "away", "score": {"value": away_points, "displayValue": str(away_points), "winner": away_points > home_points}, "team": {"id": "3", "abbreviation": "INTL", "displayName": "INTL"}},
        ],
    }


def _match_entry(match_id, match_time, description="Thursday Foursomes", won=True):
    return {
        "id": match_id, "date": match_time, "description": description, "type": {"id": "5", "text": "foursome"},
        "scoringSystem": {"id": "2", "name": "Match"},
        "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post"}},
        "competitors": [
            _match_competitor("home", "1", "USA", [("1085", "Tony Finau")], won=won, margin="6 & 5", value=6.0),
            _match_competitor("away", "3", "INTL", [("2001", "Hideki Matsuyama")]),
        ],
    }


def _espn_cup_event(tournament_id, sessions=None, tournament_name="Presidents Cup"):
    return {
        "id": tournament_id,
        "date": "2026-09-24T04:00Z",
        "endDate": "2026-09-27T04:00Z",
        "season": {"year": 2026},
        "seasonType": {"id": "2"},
        "tournament": {"displayName": tournament_name, "scoringSystem": {"name": "Match"}},
        "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in", "completed": False}},
        "courses": [{"id": "83", "name": "Quail Hollow Club", "host": True, "address": {"city": "Charlotte", "state": "NC"}}],
        "competitions": sessions if sessions is not None else [
            [_cup_summary_entry()],
            [_match_entry("10951", "2026-09-24T17:05Z"), _match_entry("10956", "2026-09-24T17:17Z")],
        ],
    }


class TestInTournamentRange:
    def test_false_when_day_is_before_event_date(self):
        event = _field_event("1", "2026-08-20", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 19)) is False

    def test_false_when_day_is_after_end_date(self):
        event = _field_event("1", "2026-08-20", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 24)) is False

    def test_true_within_the_range_inclusive(self):
        event = _field_event("1", "2026-08-20", "2026-08-23")
        assert live_scores._in_tournament_range(event, date(2026, 8, 20)) is True
        assert live_scores._in_tournament_range(event, date(2026, 8, 23)) is True

    def test_missing_end_date_falls_back_to_a_single_day_range(self):
        event = _field_event("1", "2026-08-20", None)
        assert live_scores._in_tournament_range(event, date(2026, 8, 20)) is True
        assert live_scores._in_tournament_range(event, date(2026, 8, 21)) is False

    def test_unparseable_event_date_returns_false(self):
        event = _field_event("1", "not-a-date", "2026-08-23")
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

    def test_cup_event_checks_its_own_top_level_status_not_participants(self):
        # A cup's own participants carry no status field at all -- must
        # not be treated as "no participants -- active" nor crash.
        active_cup = {"event_type": "cup", "status": "scheduled", "participants": [{"result": {"points": 10, "won": False, "halved": False}}]}
        completed_cup = {"event_type": "cup", "status": "completed", "participants": [{"result": {"points": 17.5, "won": True, "halved": False}}]}
        assert live_scores._is_active(active_cup) is True
        assert live_scores._is_active(completed_cup) is False


class TestFieldCandidates:
    def test_excludes_an_event_outside_its_tournament_range(self):
        now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._field_candidates(storage, SPORT, now, {})

        assert candidates == []

    def test_excludes_an_event_more_than_an_hour_before_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)  # 2h before a 13:00 tee time
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._field_candidates(storage, SPORT, now, {})

        assert candidates == []

    def test_includes_an_event_within_an_hour_of_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)  # 30min before a 13:00 tee time
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._field_candidates(storage, SPORT, now, {})

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_includes_an_event_past_its_next_tee_time(self):
        now = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event("1", "2026-08-20", "2026-08-23", "2026-08-20T13:00Z")]

        candidates = live_scores._field_candidates(storage, SPORT, now, {})

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_includes_an_event_with_no_known_tee_time_yet_within_range(self):
        now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event("1", "2026-08-20", "2026-08-23", None)]

        candidates = live_scores._field_candidates(storage, SPORT, now, {})

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_includes_an_event_within_the_end_buffer_tail_even_outside_the_window(self):
        now = datetime(2026, 8, 20, 23, 59, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event("1", "2026-08-20", "2026-08-20", "2026-08-20T13:00Z")]
        last_active_at = {"1": (now - timedelta(minutes=30)).isoformat()}

        candidates = live_scores._field_candidates(storage, SPORT, now, last_active_at)

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_ignores_match_play_and_cup_rows(self):
        now = datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:05Z"),
            _cup_row("t", "2026-09-24", "2026-09-27"),
        ]

        candidates = live_scores._field_candidates(storage, SPORT, now, {})

        assert candidates == []


class TestMatchCupTournamentIds:
    def test_excludes_a_tournament_more_than_an_hour_before_its_earliest_match_time(self):
        now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)  # 2h before 17:05
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:05Z"),
            _cup_row("t", "2026-09-24", "2026-09-27"),
        ]

        assert live_scores._match_cup_tournament_ids(storage, SPORT, now, {}) == []

    def test_includes_a_tournament_within_an_hour_of_any_of_its_matches(self):
        now = datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc)  # 35min before 17:05
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:05Z"),
            _match_row("t-match-10956", "t", "2026-09-24", "2026-09-27", "2026-09-24T20:00Z"),
            _cup_row("t", "2026-09-24", "2026-09-27"),
        ]

        assert live_scores._match_cup_tournament_ids(storage, SPORT, now, {}) == ["t"]

    def test_dedups_multiple_match_rows_sharing_one_parent_into_one_tournament_id(self):
        now = datetime(2026, 9, 24, 17, 10, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:05Z"),
            _match_row("t-match-10956", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:17Z"),
            _match_row("t-match-10962", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:20Z"),
            _cup_row("t", "2026-09-24", "2026-09-27"),
        ]

        assert live_scores._match_cup_tournament_ids(storage, SPORT, now, {}) == ["t"]

    def test_falls_back_to_tournament_range_when_no_match_time_is_known_yet(self):
        now = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-27", None),
        ]

        assert live_scores._match_cup_tournament_ids(storage, SPORT, now, {}) == ["t"]

    def test_includes_a_tournament_within_the_end_buffer_tail_even_outside_the_window(self):
        now = datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc)  # well past any match time
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-27", "2026-09-24T17:05Z"),
            _cup_row("t", "2026-09-24", "2026-09-27"),
        ]
        last_active_at = {"t-match-10951": (now - timedelta(minutes=30)).isoformat()}

        assert live_scores._match_cup_tournament_ids(storage, SPORT, now, last_active_at) == ["t"]

    def test_excludes_a_tournament_more_than_an_hour_past_last_active(self):
        now = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_row("t-match-10951", "t", "2026-09-24", "2026-09-24", "2026-09-24T17:05Z"),
            _cup_row("t", "2026-09-24", "2026-09-24"),
        ]
        last_active_at = {"t-match-10951": (now - timedelta(hours=2)).isoformat()}

        assert live_scores._match_cup_tournament_ids(storage, SPORT, now, last_active_at) == []


class TestRefresh:
    """refresh() itself reads datetime.now() internally, so every fixture
    here is built relative to the real wall clock at test time -- see
    module docstring."""

    def _current_field_event(self, event_id: str) -> dict:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        next_tee_time = (now - timedelta(minutes=30)).isoformat()
        return _field_event(event_id, today, today, next_tee_time)

    def _current_match_row(self, event_id: str, tournament_id: str) -> dict:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        match_time = (now - timedelta(minutes=30)).isoformat()
        return _match_row(event_id, tournament_id, today, today, match_time)

    def test_no_candidates_never_calls_espn(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        client = MagicMock()
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}
        client.get_leaderboard.assert_not_called()
        s3.put_object.assert_not_called()

    def test_a_field_candidate_gets_fetched_and_cached(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_field_event("1")]
        client = MagicMock()
        client.get_leaderboard.return_value = {"events": [_espn_event("1", competitors=[_competitor("10")])]}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 1}
        client.get_leaderboard.assert_called_once_with("1")
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["event_type"] == "field"
        assert cached["events"]["1"]["tournament_name"] == "BMW Championship"
        assert cached["events"]["1"]["participants"]["10"]["status"] == "finished"

    def test_a_non_stroke_play_field_candidate_is_skipped_not_errored(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_field_event("1")]
        client = MagicMock()
        match_event = _espn_event("1")
        match_event["tournament"]["scoringSystem"]["name"] = "Match"
        client.get_leaderboard.return_value = {"events": [match_event]}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}

    def test_one_field_candidates_fetch_failure_does_not_kill_the_others(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_field_event("1"), self._current_field_event("2")]
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

    def test_a_match_cup_tournament_fetches_once_and_caches_every_match_plus_the_cup(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            self._current_match_row("t-match-10951", "t"),
            self._current_match_row("t-match-10956", "t"),
            _cup_row("t", "2000-01-01"),  # date range irrelevant here -- match_time already gates it
        ]
        client = MagicMock()
        client.get_leaderboard.return_value = {"events": [_espn_cup_event("t")]}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        client.get_leaderboard.assert_called_once_with("t")
        assert result == {"polled": 3}  # 2 matches + 1 cup, from ONE ESPN call
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["t-match-10951"]["event_type"] == "match_play"
        assert cached["events"]["t-match-10951"]["participants"]["1"]["won"] is True
        assert cached["events"]["t"]["event_type"] == "cup"
        assert cached["events"]["t"]["participants"]["1"]["points"] == 17.5

    def test_a_non_supported_match_play_tournament_is_skipped_not_errored(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_match_row("t-match-10951", "t")]
        client = MagicMock()
        medal_event = _espn_cup_event("t")
        medal_event["tournament"]["scoringSystem"]["name"] = "Medal"
        client.get_leaderboard.return_value = {"events": [medal_event]}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}

    def test_a_field_and_a_match_cup_tournament_are_both_polled_in_one_tick(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [
            self._current_field_event("1"),
            self._current_match_row("t-match-10951", "t"),
            _cup_row("t", "2000-01-01"),
        ]
        client = MagicMock()

        def _get_leaderboard(event_id):
            if event_id == "1":
                return {"events": [_espn_event("1", competitors=[_competitor("10")])]}
            return {"events": [_espn_cup_event("t", sessions=[[_cup_summary_entry()], [_match_entry("10951", "2026-09-24T17:05Z")]])]}

        client.get_leaderboard.side_effect = _get_leaderboard
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 3}  # field + 1 match + cup
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["event_type"] == "field"
        assert cached["events"]["t-match-10951"]["event_type"] == "match_play"
        assert cached["events"]["t"]["event_type"] == "cup"

    def test_a_still_active_event_records_last_active_at_for_the_end_buffer_tail(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [self._current_field_event("1")]
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
        storage.get_all_events.return_value = [self._current_field_event("1")]
        client = MagicMock()
        client.get_leaderboard.return_value = {
            "events": [_espn_event("1", competitors=[_competitor("10", "STATUS_FINISH")], completed=True)],
        }
        s3 = _make_s3()
        prior_active_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": prior_active_at,
            "events": {"1": {"event_type": "field", "status": "scheduled", "tournament_name": "BMW Championship", "participants": {}, "last_active_at": prior_active_at}},
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
            "events": {"1": {"event_type": "field", "status": "scheduled", "tournament_name": "BMW Championship", "participants": {}}},
        }).encode("utf-8")

        result = live_scores.get_live_scores(s3, BUCKET)

        assert result["events"]["1"]["tournament_name"] == "BMW Championship"

    def test_returns_empty_when_the_cache_is_stale(self):
        s3 = _make_s3()
        stale_fetch = datetime.now(timezone.utc) - live_scores.STALE_AFTER - timedelta(minutes=1)
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": stale_fetch.isoformat(),
            "events": {"1": {"event_type": "field", "status": "scheduled", "tournament_name": "BMW Championship", "participants": {}}},
        }).encode("utf-8")

        result = live_scores.get_live_scores(s3, BUCKET)

        assert result == {"events": {}}
