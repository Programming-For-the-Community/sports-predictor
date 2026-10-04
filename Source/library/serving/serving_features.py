"""
Captures the model inputs behind an event's pre-event snapshot so they can be
compared with the training rows rebuilt for the same event.
"""
import logging
import math
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from library.storage.serving_features import serving_features_key

logger = logging.getLogger(__name__)

_captured: ContextVar[dict | None] = ContextVar("serving_features", default=None)


def capture_key(model_card: dict, feature_row: dict) -> str:
    """model#vN, plus #entity for a per-participant row."""
    key = f"{model_card.get('model_name')}#v{model_card.get('version')}"
    entity_id = feature_row.get("entity_id")
    return f"{key}#{entity_id}" if entity_id is not None else key


def note(model_card: dict, feature_row: dict, model_inputs: dict[str, float]) -> None:
    """Records `model_inputs` while capturing; a no-op otherwise."""
    captured = _captured.get()
    if captured is not None:
        captured[capture_key(model_card, feature_row)] = {
            column: None if math.isnan(value) else value for column, value in model_inputs.items()
        }


@contextmanager
def capturing() -> Generator[dict, None, None]:
    captured: dict = {}
    token = _captured.set(captured)
    try:
        yield captured
    finally:
        _captured.reset(token)


def write(s3, sport: str, event_key: str, captured: dict) -> None:
    """Best-effort: a failed write never fails the snapshot it follows."""
    if not captured:
        return
    try:
        s3.put_json(serving_features_key(sport, event_key), captured)
    except Exception:
        logger.exception("Failed writing serving features for %s", event_key)
