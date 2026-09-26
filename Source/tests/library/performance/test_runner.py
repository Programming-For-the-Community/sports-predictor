from datetime import date
from unittest.mock import patch

from library.performance import runner
from test_head_to_head import _event, _row, _rows


class _Storage:
    def __init__(self, events, stat_lines=None, upcoming=None):
        self.events = events
        self.stat_lines = stat_lines or {}
        self.upcoming = upcoming or []
        self.requested = None
        self.upcoming_requested = None

    def get_all_events(self, sport, status, since_date=None, scan_index_forward=False, limit=None):
        if status == "scheduled":
            self.upcoming_requested = (sport, since_date, scan_index_forward, limit)
            return self.upcoming[:limit]
        self.requested = (sport, status, since_date)
        return self.events

    def get_player_game_stats_for_event(self, event_key):
        return self.stat_lines.get(event_key, [])


class _Table:
    def __init__(self, rows_by_event):
        self.rows_by_event = rows_by_event

    def query(self, condition, **kwargs):
        event_key = condition.get_expression()["values"][1]
        return list(self.rows_by_event.get(event_key, []))


class _S3:
    def __init__(self):
        self.written = {}

    def put_json(self, key, payload):
        self.written[key] = payload


CARDS = [{"model_name": "win-probability", "version": 9, "accuracy": 0.66}, {"model_name": "player-prop-passing-yards", "version": 4, "mae": 50.0}]


def _run(storage, table, s3=None):
    s3 = s3 or _S3()
    with patch.object(runner, "list_models", return_value={"models": CARDS}):
        return runner.run_sport("nfl", storage, table, s3, date(2026, 9, 30)), s3


def _season_event(event_id, season, **kwargs):
    return {**_event(event_id, *kwargs.pop("scores", (27, 20)), **kwargs), "season": season}


def test_grades_only_the_current_season_and_stores_the_scorecard_under_the_sports_key():
    old = _season_event("0", 2025)
    current = _season_event("1", 2026)
    table = _Table({old["event_key"]: _rows(), current["event_key"]: _rows()})

    document, s3 = _run(_Storage([old, current]), table)

    assert document["season"] == 2026
    assert s3.written["model-performance/nfl/latest.json"] is document
    win = next(m for m in document["models"] if m["model_name"] == "win-probability")
    assert win["season"]["n"] == 1


def test_looks_back_far_enough_to_reach_the_start_of_the_season():
    storage = _Storage([])

    _run(storage, _Table({}))

    sport, status, since = storage.requested
    assert (sport, status) == ("nfl", "completed")
    assert since < "2025-09-01"


def test_stat_lines_are_fetched_only_for_events_with_player_prop_predictions():
    with_props = _season_event("1", 2026)
    without = _season_event("2", 2026)
    rows = _rows() + [_row("MODEL#player-prop-passing-yards#v4#PLAYER#p1", {"value": 250.0})]
    storage = _Storage([with_props, without], {with_props["event_key"]: [{"entity_id": "p1", "stat_line": {"passing_yards": 300}}]})
    fetched = []
    original = storage.get_player_game_stats_for_event
    storage.get_player_game_stats_for_event = lambda key: fetched.append(key) or original(key)

    document, _ = _run(storage, _Table({with_props["event_key"]: rows, without["event_key"]: _rows()}))

    assert fetched == [with_props["event_key"]]
    props = next(m for m in document["models"] if m["model_name"] == "player-prop-passing-yards")
    assert props["season"] == {"value": 50.0, "n": 1}


def test_coverage_counts_completed_events_that_were_never_predicted():
    predicted = _season_event("1", 2026)
    unpredicted = _season_event("2", 2026)

    document, _ = _run(_Storage([predicted, unpredicted]), _Table({predicted["event_key"]: _rows()}))

    assert document["coverage"] == {"completed_events": 2, "with_prediction": 1, "unpredicted": 1}


def test_the_week_of_the_next_unfinished_game_is_left_out_of_last_week():
    finished_week = _season_event("1", 2026, week=3, event_date="2026-09-20")
    open_week = _season_event("2", 2026, week=4, event_date="2026-09-25")
    upcoming = _season_event("3", 2026, week=4, event_date="2026-09-30")
    table = _Table({finished_week["event_key"]: _rows(), open_week["event_key"]: _rows()})
    storage = _Storage([finished_week, open_week], upcoming=[upcoming])

    document, _ = _run(storage, table)

    win = next(m for m in document["models"] if m["model_name"] == "win-probability")
    assert win["last_period"]["label"] == "Wk 3"
    assert win["season"]["n"] == 2
    assert storage.upcoming_requested == ("nfl", "2026-09-30", True, 1)


def test_with_nothing_left_to_play_the_latest_week_is_last_week():
    event = _season_event("1", 2026, week=4)

    document, _ = _run(_Storage([event]), _Table({event["event_key"]: _rows()}))

    win = next(m for m in document["models"] if m["model_name"] == "win-probability")
    assert win["last_period"]["label"] == "Wk 4"


def test_a_sport_with_no_completed_events_writes_an_empty_scorecard():
    document, s3 = _run(_Storage([]), _Table({}))

    assert document["season"] is None
    assert document["coverage"]["completed_events"] == 0
    assert "model-performance/nfl/latest.json" in s3.written


class TestFieldSport:
    def _golfer_event(self, event_id, season=2026):
        return {
            "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_date": "2026-09-17", "event_type": "field",
            "season": season, "tournament_name": "Biltmore Championship Asheville",
            "participants": [{"entity_id": "1", "result": {"finish_position": 3, "status": "finished", "score_to_par": -10, "rounds": []}}],
        }

    def test_grades_pga_from_the_raw_rows_and_writes_a_per_event_scorecard(self):
        event = self._golfer_event("9")
        table = _Table({event["event_key"]: [_row("MODEL#top-10-probability#v5#GOLFER#1", {"value": 0.7})]})
        cards = [{"model_name": "top-10-probability", "version": 5, "accuracy": 0.9}]
        s3 = _S3()

        with patch.object(runner, "list_models", return_value={"models": cards}):
            document = runner.run_sport("pga", _Storage([event]), table, s3, date(2026, 9, 30))

        assert document["period_kind"] == "event"
        record = document["models"][0]
        assert record["model_name"] == "top-10-probability"
        assert record["season"] == {"value": 1.0, "n": 1}
        assert s3.written["model-performance/pga/latest.json"] is document
        assert document["coverage"] == {"completed_events": 1, "with_prediction": 1, "unpredicted": 0}


class TestRollingWindow:
    def test_ncaambb_is_graded_on_the_last_seven_days_and_says_so(self):
        storage = _Storage([])

        with patch.object(runner, "list_models", return_value={"models": []}):
            document = runner.run_sport("ncaambb", storage, _Table({}), _S3(), date(2026, 3, 10))

        _, _, since = storage.requested
        assert since == "2026-03-03"
        assert document["window_days"] == 7

    def test_other_sports_are_graded_on_the_whole_season_and_do_not_claim_a_window(self):
        storage = _Storage([])

        with patch.object(runner, "list_models", return_value={"models": []}):
            document = runner.run_sport("nfl", storage, _Table({}), _S3(), date(2026, 9, 30))

        assert storage.requested[2] < "2025-09-01"
        assert "window_days" not in document
