"""
Unit tests for library.aws.xray -- direct-to-API X-Ray segment emission
for short-lived batch tasks (feature-engineering, model-training), no
daemon/sidecar. The module-level `_xray` boto3 client is patched
directly, same pattern test_ec2_training_reaper.py uses for its own
module-level clients.
"""
import json
import re
from unittest.mock import patch

import pytest

from library.aws import xray


class TestNewTraceId:
    def test_matches_x_rays_own_trace_id_format(self):
        assert re.fullmatch(r"1-[0-9a-f]{8}-[0-9a-f]{24}", xray.new_trace_id())

    def test_two_calls_produce_different_ids(self):
        assert xray.new_trace_id() != xray.new_trace_id()


class TestCurrentTraceHeader:
    def test_returns_none_when_env_var_is_unset(self, monkeypatch):
        monkeypatch.delenv("_X_AMZN_TRACE_ID", raising=False)
        assert xray.current_trace_header() is None

    def test_parses_root_and_parent(self, monkeypatch):
        monkeypatch.setenv("_X_AMZN_TRACE_ID", "Root=1-5e1b4151-5ac6c58dc39c531a0d6f9a3f;Parent=53995c3f42cd8ad8;Sampled=1")
        assert xray.current_trace_header() == ("1-5e1b4151-5ac6c58dc39c531a0d6f9a3f", "53995c3f42cd8ad8")

    def test_returns_none_when_parent_is_missing(self, monkeypatch):
        """Not-sampled invocations carry a header with no Parent."""
        monkeypatch.setenv("_X_AMZN_TRACE_ID", "Root=1-5e1b4151-5ac6c58dc39c531a0d6f9a3f;Sampled=0")
        assert xray.current_trace_header() is None


class TestIndependentSegment:
    def test_emits_one_segment_with_no_parent_id(self):
        with patch.object(xray, "_xray") as mock_xray:
            with xray.independent_segment("nfl-train-win-probability-model"):
                pass

        assert mock_xray.put_trace_segments.call_count == 1
        document = json.loads(mock_xray.put_trace_segments.call_args.kwargs["TraceSegmentDocuments"][0])
        assert document["name"] == "nfl-train-win-probability-model"
        assert "parent_id" not in document
        assert re.fullmatch(r"1-[0-9a-f]{8}-[0-9a-f]{24}", document["trace_id"])
        assert document["start_time"] <= document["end_time"]

    def test_carries_annotations(self):
        with patch.object(xray, "_xray") as mock_xray:
            with xray.independent_segment("pga-train-cup-winprob-model", annotations={"training_run_id": "run-123"}):
                pass

        document = json.loads(mock_xray.put_trace_segments.call_args.kwargs["TraceSegmentDocuments"][0])
        assert document["annotations"] == {"training_run_id": "run-123"}

    def test_marks_fault_and_reraises_on_exception(self):
        with patch.object(xray, "_xray") as mock_xray:
            with pytest.raises(ValueError):
                with xray.independent_segment("nfl-train-win-probability-model"):
                    raise ValueError("boom")

        document = json.loads(mock_xray.put_trace_segments.call_args.kwargs["TraceSegmentDocuments"][0])
        assert document["fault"] is True

    def test_a_put_trace_segments_failure_is_swallowed_not_raised(self):
        """A training run's own result shouldn't be lost over X-Ray being
        unavailable -- same "log and move on" precedent every other best-
        effort AWS write in this project follows."""
        with patch.object(xray, "_xray") as mock_xray:
            mock_xray.put_trace_segments.side_effect = RuntimeError("X-Ray unavailable")
            with xray.independent_segment("nfl-train-win-probability-model"):
                pass  # does not raise


class TestLinkedSegment:
    def test_emits_a_subsegment_carrying_the_given_trace_and_parent_ids(self):
        with patch.object(xray, "_xray") as mock_xray:
            with xray.linked_segment("nfl-feature-engineering", "1-aaaaaaaa-bbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccc"):
                pass

        document = json.loads(mock_xray.put_trace_segments.call_args.kwargs["TraceSegmentDocuments"][0])
        assert document["trace_id"] == "1-aaaaaaaa-bbbbbbbbbbbbbbbbbbbbbbbb"
        assert document["parent_id"] == "cccccccccccccccc"
        assert document["type"] == "subsegment"


class TestLinkedSegmentFromEnv:
    def test_is_a_no_op_when_trace_id_is_unset(self, monkeypatch):
        monkeypatch.delenv("XRAY_TRACE_ID", raising=False)
        monkeypatch.setenv("XRAY_PARENT_ID", "cccccccccccccccc")
        with patch.object(xray, "_xray") as mock_xray:
            with xray.linked_segment_from_env("nfl-feature-engineering"):
                pass

        mock_xray.put_trace_segments.assert_not_called()

    def test_is_a_no_op_when_trace_id_is_an_empty_string(self, monkeypatch):
        """season-gate's own handler sends "" rather than omitting the
        key whenever ITS OWN invocation wasn't traced -- this is the
        far-end no-op that expects."""
        monkeypatch.setenv("XRAY_TRACE_ID", "")
        monkeypatch.setenv("XRAY_PARENT_ID", "")
        with patch.object(xray, "_xray") as mock_xray:
            with xray.linked_segment_from_env("nfl-feature-engineering"):
                pass

        mock_xray.put_trace_segments.assert_not_called()

    def test_a_manual_run_outside_the_orchestrator_still_runs_the_wrapped_code(self, monkeypatch):
        monkeypatch.delenv("XRAY_TRACE_ID", raising=False)
        monkeypatch.delenv("XRAY_PARENT_ID", raising=False)
        ran = False
        with patch.object(xray, "_xray"):
            with xray.linked_segment_from_env("nfl-feature-engineering"):
                ran = True

        assert ran

    def test_emits_when_both_are_set(self, monkeypatch):
        monkeypatch.setenv("XRAY_TRACE_ID", "1-aaaaaaaa-bbbbbbbbbbbbbbbbbbbbbbbb")
        monkeypatch.setenv("XRAY_PARENT_ID", "cccccccccccccccc")
        with patch.object(xray, "_xray") as mock_xray:
            with xray.linked_segment_from_env("nfl-feature-engineering"):
                pass

        document = json.loads(mock_xray.put_trace_segments.call_args.kwargs["TraceSegmentDocuments"][0])
        assert document["trace_id"] == "1-aaaaaaaa-bbbbbbbbbbbbbbbbbbbbbbbb"
        assert document["parent_id"] == "cccccccccccccccc"
