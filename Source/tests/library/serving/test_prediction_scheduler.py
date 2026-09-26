from datetime import datetime, timedelta, timezone

from library.serving import prediction_scheduler as scheduler
from test_prediction_snapshots import FakeTable

NOW = datetime(2026, 9, 27, 16, 0, tzinfo=timezone.utc)


class StateTable(FakeTable):
    """FakeTable + get_item, and put_item honoring not-exists conditions."""

    def get_item(self, key):
        return next((r for r in self.rows if r["event_key"] == key["event_key"] and r["model_key"] == key["model_key"]), None)

    def put_item(self, item, condition_expression=None):
        existing = self.get_item(item)
        if condition_expression is not None and existing is not None:
            return False
        if existing is not None:
            self.rows.remove(existing)
        self.rows.append(item)
        return True


def _event(event_id, minutes_to_kickoff, **overrides):
    kickoff = NOW + timedelta(minutes=minutes_to_kickoff)
    return {
        "event_key": f"SPORT#NFL#EVENT#{event_id}", "event_id": event_id, "kickoff_time": kickoff.isoformat(),
        "event_date": "2026-09-27", "season": 2026, "season_type": 2, "week": 3, **overrides,
    }


def _run(events, table=None, sports=None, now=NOW):
    calls = []
    table = table if table is not None else StateTable([])
    summary = scheduler.run_tick(
        now, sports or {"nfl": scheduler.SPORT_SCHEDULES["nfl"]}, lambda sport: events, table,
        lambda name, payload: calls.append((name, payload)), "proj",
    )
    return summary, calls, table


class TestRefreshWindow:
    def test_event_between_30_and_15_minutes_out_triggers_one_forced_ingest(self):
        _, calls, _ = _run([_event("1", 25)])

        assert calls == [("proj-nfl-ingest", {"season": 2026, "season_type": 2, "week": 3, "force_refresh": True})]

    def test_events_in_the_same_week_share_one_ingest(self):
        _, calls, _ = _run([_event("1", 25), _event("2", 22)])

        assert len(calls) == 1

    def test_refresh_is_attempted_only_once_per_event(self):
        table = StateTable([])
        _run([_event("1", 25)], table)

        _, calls, _ = _run([_event("1", 20)], table)

        assert calls == []

    def test_more_than_30_minutes_out_does_nothing(self):
        _, calls, _ = _run([_event("1", 45)])

        assert calls == []

    def test_a_sport_without_refresh_data_skips_straight_to_snapshot(self):
        sports = {"ncaafb": scheduler.SPORT_SCHEDULES["ncaafb"]}

        _, calls, _ = _run([_event("1", 25)], sports=sports)
        assert calls == []

        _, calls, _ = _run([_event("1", 10)], sports=sports)
        assert calls == [("proj-ncaafb-predict", {"detail-type": "SnapshotPrediction", "event_id": "1"})]

    def test_basketball_refresh_is_keyed_by_scoreboard_date(self):
        sports = {"nba": scheduler.SPORT_SCHEDULES["nba"]}

        _, calls, _ = _run([_event("1", 25)], sports=sports)

        assert calls == [("proj-nba-ingest", {"date": "20260927", "force_refresh": True})]


class TestSnapshotWindow:
    def test_event_within_15_minutes_triggers_a_snapshot(self):
        summary, calls, _ = _run([_event("1", 10)])

        assert calls == [("proj-nfl-predict", {"detail-type": "SnapshotPrediction", "event_id": "1"})]
        assert summary == {"nfl": {"refreshes": 0, "snapshots": 1}}

    def test_an_already_snapshotted_event_is_left_alone(self):
        table = StateTable([{"event_key": "SPORT#NFL#EVENT#1", "model_key": "SNAPSHOT#final_pregame#MODEL#x#v1", "generated_at": "t"}])

        _, calls, _ = _run([_event("1", 10)], table)

        assert calls == []

    def test_a_repeat_tick_does_not_double_fire_while_the_first_attempt_is_fresh(self):
        table = StateTable([])
        _run([_event("1", 12)], table)

        _, calls, _ = _run([_event("1", 8)], table, now=NOW + timedelta(minutes=4))

        assert calls == []

    def test_a_failed_attempt_is_retried_after_the_retry_window(self):
        table = StateTable([])
        _run([_event("1", 14)], table)

        _, calls, _ = _run([_event("1", 14)], table, now=NOW + timedelta(minutes=8))

        assert [c[0] for c in calls] == ["proj-nfl-predict"]

    def test_started_events_and_events_without_a_kickoff_are_ignored(self):
        _, calls, _ = _run([_event("1", -5), _event("2", 10, kickoff_time=None)])

        assert calls == []


def test_upcoming_events_queries_a_narrow_date_range_off_the_status_index():
    class Recorder:
        def query(self, condition, index_name=None, **kwargs):
            self.args = (condition.get_expression(), index_name)
            return []

    table = Recorder()
    scheduler.upcoming_events(table, "nfl", NOW)

    expression, index_name = table.args
    assert index_name == "sport-status-index"
    assert expression["operator"] == "AND"


class TestStartOfEventSnapshot:
    """PGA and F1: ONE snapshot at the start of the event, from 11:00 UTC on."""

    def _event(self, event_type="field", event_date="2026-09-17"):
        return {"event_key": "SPORT#X#EVENT#9", "event_id": "9", "event_type": event_type, "event_date": event_date}

    def _tick(self, sport, event, table=None, when=datetime(2026, 9, 17, 11, 5, tzinfo=timezone.utc)):
        table = table if table is not None else StateTable([])
        calls = []
        scheduler.run_tick(
            when, {sport: scheduler.SPORT_SCHEDULES[sport]}, lambda s: [event], table, lambda n, p: calls.append((n, p)), "proj",
        )
        return calls, table

    def test_pga_snapshots_a_field_event_on_its_first_day(self):
        calls, _ = self._tick("pga", self._event())

        assert calls == [("proj-pga-predict", {"detail-type": "SnapshotPrediction", "event_id": "9"})]

    def test_pga_also_tries_the_day_before_so_a_failed_attempt_has_a_retry(self):
        calls, _ = self._tick("pga", self._event(), when=datetime(2026, 9, 16, 11, 5, tzinfo=timezone.utc))

        assert len(calls) == 1

    def test_pga_does_not_snapshot_once_the_tournament_is_under_way(self):
        calls, _ = self._tick("pga", self._event(), when=datetime(2026, 9, 18, 11, 5, tzinfo=timezone.utc))

        assert calls == []

    def test_f1_snapshots_on_race_day_only_never_the_day_before(self):
        before, _ = self._tick("f1", self._event(), when=datetime(2026, 9, 16, 11, 5, tzinfo=timezone.utc))
        race_day, _ = self._tick("f1", self._event())

        assert before == []
        assert race_day == [("proj-f1-predict", {"detail-type": "SnapshotPrediction", "event_id": "9"})]

    def test_f1_covers_sprints_too_but_pga_ignores_match_play(self):
        sprint, _ = self._tick("f1", self._event(event_type="sprint"))
        match_play, _ = self._tick("pga", self._event(event_type="match_play"))

        assert len(sprint) == 1
        assert match_play == []

    def test_waits_until_after_the_daily_ingest_has_landed(self):
        calls, _ = self._tick("f1", self._event(), when=datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc))

        assert calls == []

    def test_stops_once_the_events_snapshot_exists(self):
        done = {"event_key": "SPORT#X#EVENT#9", "model_key": "SNAPSHOT#final_pregame#MODEL#round-1#v3#GOLFER#1", "generated_at": "t"}

        calls, _ = self._tick("pga", self._event(), StateTable([done]))

        assert calls == []

    def test_does_not_double_fire_while_a_fresh_attempt_is_in_flight_but_retries_after_the_window(self):
        event = self._event()
        _, table = self._tick("f1", event)

        in_flight, _ = self._tick("f1", event, table, when=datetime(2026, 9, 17, 11, 10, tzinfo=timezone.utc))
        retried, _ = self._tick("f1", event, table, when=datetime(2026, 9, 17, 11, 20, tzinfo=timezone.utc))

        assert in_flight == []
        assert len(retried) == 1
