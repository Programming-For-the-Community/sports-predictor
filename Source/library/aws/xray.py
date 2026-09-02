"""
Direct-to-API X-Ray segment emission for short-lived batch tasks
(feature-engineering, model-training) -- one PutTraceSegments call per
task, no X-Ray daemon/sidecar needed, matching this project's direct-
boto3 style everywhere else in library/aws/.

Two shapes, matching the two positions a task can be in relative to
sfn-training-orchestrator.tf's own trace:

- linked_segment/linked_segment_from_env -- joins an existing trace as a
  connected node (RunFeatureEngineering, whose parent state -- ForEachSport
  -- is an inline Map, so Step Functions' own native Lambda tracing can
  hand a trace_id/parent_id down to it via CheckSeason's Lambda invoke).
- independent_segment -- a fresh, unlinked trace of its own (every
  training task: TrainAllTargets is a Distributed Map, and AWS doesn't
  propagate X-Ray trace context into a Distributed Map's child workflow
  executions at all -- see AWS's own Step Functions X-Ray tracing docs --
  so there's no parent trace context to inherit in the first place).
  Still shows up as its own node in the X-Ray Trace Map, and is
  correlatable across sport/target via the training_run_id annotation.
"""
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager

import boto3

logger = logging.getLogger("xray")

# Explicit region_name -- see dynamodb_table.py's/season_gate's own
# handler.py for why an unqualified boto3.client() raises NoRegionError
# in CI's test-collection environment (no AWS_REGION set there at all).
_REGION = os.environ.get("AWS_REGION", "us-east-2")
_xray = boto3.client("xray", region_name=_REGION)


def new_trace_id() -> str:
    """A fresh X-Ray trace ID in AWS's own format: 1-{8 hex epoch
    seconds}-{24 hex random}."""
    return f"1-{int(time.time()):08x}-{secrets.token_hex(12)}"


def _new_id() -> str:
    return secrets.token_hex(8)


def current_trace_header() -> tuple[str, str] | None:
    """Parses Lambda's own _X_AMZN_TRACE_ID env var -- set automatically
    on every invocation once the function's own tracing_config is Active
    -- into (trace_id, segment_id): what a caller needs to hand a
    downstream, non-natively-traced service (ECS -- see this module's own
    docstring) to link it into this invocation's own trace. None if unset
    (tracing not active, or a local/manual invocation) or unparseable."""
    header = os.environ.get("_X_AMZN_TRACE_ID")
    if not header:
        return None
    parts = dict(part.split("=", 1) for part in header.split(";") if "=" in part)
    root, parent = parts.get("Root"), parts.get("Parent")
    if not root or not parent:
        return None
    return root, parent


def _emit(trace_id: str, segment_id: str, name: str, start: float, end: float,
          parent_id: str | None = None, fault: bool = False, annotations: dict | None = None) -> None:
    document = {"trace_id": trace_id, "id": segment_id, "name": name, "start_time": start, "end_time": end}
    if parent_id:
        # A segment carrying parent_id without being nested inside its
        # parent's own document is X-Ray's documented "independent
        # subsegment" shape -- what lets this cross a service boundary
        # (Lambda's own auto-created segment -> this task's own segment)
        # instead of only ever nesting within one process's single trace.
        document["parent_id"] = parent_id
        document["type"] = "subsegment"
    if fault:
        document["fault"] = True
    if annotations:
        document["annotations"] = annotations
    try:
        _xray.put_trace_segments(TraceSegmentDocuments=[json.dumps(document)])
    except Exception:
        logger.warning("Failed to emit X-Ray segment %r -- continuing without it", name, exc_info=True)


@contextmanager
def _segment(name: str, trace_id: str, parent_id: str | None = None, annotations: dict | None = None):
    segment_id = _new_id()
    start = time.time()
    fault = False
    try:
        yield
    except Exception:
        fault = True
        raise
    finally:
        _emit(trace_id, segment_id, name, start, time.time(), parent_id=parent_id, fault=fault, annotations=annotations)


@contextmanager
def linked_segment(name: str, trace_id: str, parent_id: str, annotations: dict | None = None):
    """Joins an existing trace as a linked node -- trace_id/parent_id
    inherited from an upstream native-X-Ray-traced caller (e.g. a Lambda
    invoke's own current_trace_header())."""
    with _segment(name, trace_id, parent_id=parent_id, annotations=annotations):
        yield


@contextmanager
def linked_segment_from_env(name: str, annotations: dict | None = None):
    """Same as linked_segment, reading XRAY_TRACE_ID/XRAY_PARENT_ID from
    the environment (sfn-training-orchestrator.tf's own RunFeatureEngineering
    ContainerOverrides). A no-op if either is unset or empty -- a manual/
    local run outside the orchestrator doesn't need X-Ray configured at
    all, and season_gate's own handler sends empty strings rather than
    omitting the keys whenever this invocation itself wasn't traced."""
    trace_id = os.environ.get("XRAY_TRACE_ID")
    parent_id = os.environ.get("XRAY_PARENT_ID")
    if not trace_id or not parent_id:
        yield
        return
    with linked_segment(name, trace_id, parent_id, annotations=annotations):
        yield


@contextmanager
def independent_segment(name: str, annotations: dict | None = None):
    """A fresh, unlinked trace of its own -- see this module's own
    docstring for why every training task uses this instead of
    linked_segment_from_env."""
    with _segment(name, new_trace_id(), annotations=annotations):
        yield
