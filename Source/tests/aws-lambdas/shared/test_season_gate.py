"""
Unit tests for the season-gate Lambda's handler -- a thin wrapper over
library.season.is_in_season, so these only need to confirm the event
shape is read correctly and the result shape is what the state machines'
Choice states expect. Wraparound/boundary behavior itself is covered by
tests/library/test_season.py. No AWS involved.
"""
import sys
from datetime import date
from unittest.mock import patch

shared_season_gate = sys.modules["shared_season_gate"]


class TestSeasonGateHandler:
    def test_returns_in_season_true_when_today_is_inside_the_window(self):
        with patch("shared_season_gate.current_month_day", return_value="09-15"):
            result = shared_season_gate.lambda_handler(
                {"season_start": "08-01", "season_end": "02-28"}, None
            )

        assert result == {"in_season": True}

    def test_returns_in_season_false_when_today_is_outside_the_window(self):
        with patch("shared_season_gate.current_month_day", return_value="05-15"):
            result = shared_season_gate.lambda_handler(
                {"season_start": "08-01", "season_end": "02-28"}, None
            )

        assert result == {"in_season": False}

    def test_uses_the_real_current_date_when_not_patched(self):
        today_month_day = date.today().strftime("%m-%d")
        result = shared_season_gate.lambda_handler(
            {"season_start": today_month_day, "season_end": today_month_day}, None
        )

        assert result == {"in_season": True}
