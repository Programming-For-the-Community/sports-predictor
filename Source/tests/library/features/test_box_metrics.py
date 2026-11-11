"""Unit tests for library.features.box_history and box_windows."""
import math
from unittest.mock import MagicMock

from library.features import box_history
from library.features.box_windows import CORE, Metric, MetricSet, differences, opp, own, sided


def _event(key, date, home_score=3, away_score=1, **extra):
    return {
        "event_key": key, "event_date": date, "season": 2025,
        "participants": [
            {"entity_id": "H", "role": "home", "result": {"score": home_score}},
            {"entity_id": "A", "role": "away", "result": {"score": away_score}},
        ],
        **extra,
    }


def _row(date, stat_line, opponent_line=None, key=None):
    return {"event_key": key or date, "event_date": date, "stat_line": stat_line, "opponent_stat_line": opponent_line or {}}


class TestBoxHistory:
    def test_paired_row_carries_the_opponent_line_score_and_periods(self):
        own_row = {"event_key": "E1", "event_date": "2025-01-01", "team_id": "A", "stat_line": {"x": 1}, "advanced": {"a": 1}}
        row = box_history.paired_row(_event("E1", "2025-01-01", periods=5), own_row, {"team_id": "H", "stat_line": {"x": 2}})
        assert row == {
            "event_key": "E1", "event_date": "2025-01-01", "team_id": "A", "season": 2025, "stat_line": {"x": 1},
            "opponent_stat_line": {"x": 2}, "points_for": 1, "points_against": 3, "periods": 5, "advanced": {"a": 1},
        }

    def test_paired_row_without_an_opponent_or_event(self):
        row = box_history.paired_row(None, {"team_id": "A", "stat_line": {"x": 1}}, None)
        assert row["opponent_stat_line"] == {} and row["points_for"] is None

    def test_live_paired_history_matches_training_pairing(self):
        rows = [
            {"event_key": "E1", "event_date": "2025-01-01", "team_id": "H", "stat_line": {"x": 5}},
            {"event_key": "E1", "event_date": "2025-01-01", "team_id": "A", "stat_line": {"x": 7}},
        ]
        storage = MagicMock()
        storage.get_team_game_stats_for_team.return_value = [rows[0]]
        event = _event("E1", "2025-01-01")

        live = box_history.live_paired_history(storage, "nba", "H", "2025-02-01", rows, [event], 40)

        assert live == [box_history.paired_row(event, rows[0], rows[1])]
        assert storage.get_team_game_stats_for_team.call_args.kwargs["limit"] == 40


class TestMetricSet:
    ROWS = [  # most recent first
        _row("2025-01-30", {"m": 6, "a": 10}, {"m": 1, "a": 4}),
        _row("2025-01-20", {"m": 2, "a": 10}, {"m": 3, "a": 6}),
        _row("2025-01-10", {"m": 0, "a": 0}),
        _row("2024-03-01", {"m": 9, "a": 9}),
    ]

    def test_rate_is_ratio_of_sums_per_window(self):
        rate = Metric("rate", own("m"), own("a"), windows=CORE)
        columns = MetricSet("t1", [rate]).columns(self.ROWS, 2, "2025-02-01")
        assert columns["rate"] == 8 / 20
        assert columns["rate_last10"] == 17 / 29
        assert columns["rate_season_to_date"] == 8 / 20  # the 2024 game is last season
        weights = [0.5 ** (age / 3) for age in range(4)]
        expected = (6 * weights[0] + 2 * weights[1] + 9 * weights[3]) / (10 * weights[0] + 10 * weights[1] + 9 * weights[3])
        assert math.isclose(columns["rate_ewm"], expected)

    def test_a_mean_has_no_denominator(self):
        columns = MetricSet("t2", [Metric("avg_m", own("m"))]).columns(self.ROWS, 3, "2025-02-01")
        assert columns["avg_m"] == 8 / 3

    def test_allowed_reads_the_opponents_side(self):
        rate = Metric("rate", own("m"), own("a"))
        columns = MetricSet("t3", [rate.allowed()]).columns(self.ROWS, 2, "2025-02-01")
        assert columns["rate_allowed"] == 4 / 10

    def test_games_missing_a_part_are_skipped_and_empty_windows_are_none(self):
        columns = MetricSet("t4", [Metric("x", opp("m"), opp("a"))]).columns(self.ROWS[2:3], 5, "2025-02-01")
        assert columns["x"] is None

    def test_parts_are_cached_on_the_row(self):
        rows = [_row("2025-01-01", {"m": 1})]
        calls = []
        metric = Metric("x", lambda row: calls.append(1) or 1.0)
        metric_set = MetricSet("t5", [metric])
        metric_set.columns(rows, 5, "2025-01-02")
        metric_set.columns(rows, 5, "2025-01-03")
        assert len(calls) == 1


class TestColumnHelpers:
    def test_differences_per_window(self):
        columns = {"a": 3.0, "b": 1.0, "a_ewm": 2.0, "b_ewm": None}
        assert differences(columns, "net", "a", "b", ("", "ewm")) == {"net": 2.0, "net_ewm": None}

    def test_sided_adds_diff_columns(self):
        assert sided({"x": 3, "y": 1}, {"x": 1, "y": None}, ["x", "y"]) == {
            "home_x": 3, "home_y": 1, "away_x": 1, "away_y": None, "diff_x": 2, "diff_y": None,
        }
