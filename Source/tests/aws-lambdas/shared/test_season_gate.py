"""
Unit tests for the season-gate Lambda's handler -- a thin wrapper over
library.season.is_in_season, so these only need to confirm the event
shape is read correctly and the result shape is what the state machines'
Choice states expect. Wraparound/boundary behavior itself is covered by
tests/library/test_season.py. No AWS involved.
"""
import os
import sys
from datetime import date
from unittest.mock import patch

shared_season_gate = sys.modules["shared_season_gate"]


class TestSeasonGateHandler:
    def test_returns_in_season_true_when_today_is_inside_the_window(self):
        with patch("shared_season_gate.current_month_day", return_value="09-15"), \
             patch.dict(os.environ, {}, clear=True):
            result = shared_season_gate.lambda_handler(
                {"season_start": "08-01", "season_end": "02-28"}, None
            )

        assert result == {"in_season": True, "xray_trace_id": "", "xray_parent_id": ""}

    def test_returns_in_season_false_when_today_is_outside_the_window(self):
        with patch("shared_season_gate.current_month_day", return_value="05-15"), \
             patch.dict(os.environ, {}, clear=True):
            result = shared_season_gate.lambda_handler(
                {"season_start": "08-01", "season_end": "02-28"}, None
            )

        assert result == {"in_season": False, "xray_trace_id": "", "xray_parent_id": ""}

    def test_uses_the_real_current_date_when_not_patched(self):
        today_month_day = date.today().strftime("%m-%d")
        with patch.dict(os.environ, {}, clear=True):
            result = shared_season_gate.lambda_handler(
                {"season_start": today_month_day, "season_end": today_month_day}, None
            )

        assert result == {"in_season": True, "xray_trace_id": "", "xray_parent_id": ""}

    def test_returns_empty_trace_header_when_not_traced(self):
        """No _X_AMZN_TRACE_ID env var -- a local/manual invocation, or
        this function's own tracing not Active. Empty strings, not
        omitted keys -- sfn-training-orchestrator.tf's own ResultSelector
        reads these paths unconditionally."""
        with patch.dict(os.environ, {}, clear=True):
            result = shared_season_gate.lambda_handler(
                {"season_start": "08-01", "season_end": "02-28"}, None
            )

        assert result["xray_trace_id"] == ""
        assert result["xray_parent_id"] == ""

    def test_returns_the_real_trace_header_when_traced(self):
        """Lambda sets _X_AMZN_TRACE_ID automatically once tracing_config
        is Active -- RunFeatureEngineering's own ContainerOverrides
        (sfn-training-orchestrator.tf) depends on these two values being
        parsed out correctly."""
        header = "Root=1-5e1b4151-5ac6c58dc39c531a0d6f9a3f;Parent=53995c3f42cd8ad8;Sampled=1"
        with patch.dict(os.environ, {"_X_AMZN_TRACE_ID": header}, clear=True):
            result = shared_season_gate.lambda_handler(
                {"season_start": "08-01", "season_end": "02-28"}, None
            )

        assert result["xray_trace_id"] == "1-5e1b4151-5ac6c58dc39c531a0d6f9a3f"
        assert result["xray_parent_id"] == "53995c3f42cd8ad8"
