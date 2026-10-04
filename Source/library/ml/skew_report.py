"""
Compares the model inputs recorded behind each pre-event snapshot
(library.serving.serving_features) with the training rows rebuilt for the
same events, column by column.
"""
import math
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import pandas as pd

from library.ml.training_common import load_features
from library.schema.keys import event_key as build_event_key
from library.storage.serving_features import SERVING_FEATURES_PREFIX

MAX_EXAMPLES = 3
# Captured input columns that pick one training row among several for the same participant.
_ROW_SELECTORS = ("round_number",)

MISMATCHED = "mismatched"
MISSING_AT_SERVING = "missing_at_serving"
MISSING_IN_TRAINING = "missing_in_training"


@dataclass
class ColumnSkew:
    compared: int = 0
    mismatched: int = 0
    missing_at_serving: int = 0
    missing_in_training: int = 0
    examples: list[dict] = field(default_factory=list)

    @property
    def flagged(self) -> int:
        return self.mismatched + self.missing_at_serving + self.missing_in_training


def _number(value) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and not math.isnan(value):
        return float(value)
    return None


def compare_values(served, trained) -> str | None:
    """MISMATCHED / MISSING_AT_SERVING / MISSING_IN_TRAINING, or None when they agree."""
    served_number, trained_number = _number(served), _number(trained)
    if served_number is None and trained_number is None:
        return None
    if served_number is None:
        return MISSING_AT_SERVING
    if trained_number is None:
        return MISSING_IN_TRAINING
    return None if math.isclose(served_number, trained_number, rel_tol=1e-6, abs_tol=1e-9) else MISMATCHED


def capture_entity(capture_key: str) -> str | None:
    """The participant id in a serving_features capture key, if any."""
    parts = capture_key.split("#", 2)
    return parts[2] if len(parts) == 3 else None


class TrainingRows:
    """A sport's training datasets, indexed by event_key."""

    def __init__(self, datasets: list[pd.DataFrame]):
        self._by_event = [
            (set(df.columns), dict(iter(df.groupby("event_key"))))
            for df in datasets if "event_key" in df.columns
        ]

    def find(self, event_key: str, entity_id: str | None, inputs: dict) -> dict | None:
        """The one training row holding every input column for this event (and
        participant); None when there is no single such row."""
        for columns, by_event in self._by_event:
            rows = by_event.get(event_key)
            if rows is None or not set(inputs) <= columns or (entity_id is None) == ("entity_id" in columns):
                continue
            if entity_id is not None:
                rows = rows[rows["entity_id"].astype(str) == entity_id]
            for selector in _ROW_SELECTORS:
                if selector in inputs and selector in columns:
                    rows = rows[rows[selector] == inputs[selector]]
            if len(rows) == 1:
                return rows.iloc[0].to_dict()
        return None


def _record(skew: ColumnSkew, outcome: str | None, example: dict) -> None:
    skew.compared += 1
    if outcome is None:
        return
    setattr(skew, outcome, getattr(skew, outcome) + 1)
    if len(skew.examples) < MAX_EXAMPLES:
        skew.examples.append(example)


def build_report(sport: str, captured_by_event: dict[str, dict[str, dict]], training_rows: TrainingRows) -> dict:
    """captured_by_event: event_key -> one serving_features file's contents."""
    columns: dict[str, ColumnSkew] = {}
    matched = unmatched = 0
    for event_key, captured in captured_by_event.items():
        for capture_key, inputs in captured.items():
            trained = training_rows.find(event_key, capture_entity(capture_key), inputs)
            if trained is None:
                unmatched += 1
                continue
            matched += 1
            for column, served in inputs.items():
                example = {"event_key": event_key, "capture": capture_key, "serving": served, "training": _number(trained.get(column))}
                _record(columns.setdefault(column, ColumnSkew()), compare_values(served, trained.get(column)), example)
    flagged = sorted(((name, skew) for name, skew in columns.items() if skew.flagged), key=lambda item: -item[1].flagged)
    return {
        "sport": sport,
        "events": len(captured_by_event),
        "rows_matched": matched,
        "rows_unmatched": unmatched,
        "columns_compared": len(columns),
        "flagged_columns": [
            {
                "column": name, "compared": skew.compared, MISMATCHED: skew.mismatched,
                MISSING_AT_SERVING: skew.missing_at_serving, MISSING_IN_TRAINING: skew.missing_in_training,
                "examples": skew.examples,
            }
            for name, skew in flagged
        ],
    }


def load_captured(s3, sport: str) -> dict[str, dict]:
    """event_key -> that event's serving_features file."""
    keys = [key for key in s3.list_keys(f"{SERVING_FEATURES_PREFIX}{sport}/") if key.endswith(".json")]
    return {build_event_key(sport, PurePosixPath(key).stem): s3.get_json(key) for key in keys}


def load_training_rows(s3, sport: str) -> TrainingRows:
    keys = [key for key in s3.list_keys(f"{sport}/training-data/") if key.endswith(".parquet")]
    return TrainingRows([load_features(s3, key) for key in keys])


def summary(report: dict) -> str:
    lines = [
        f"{report['sport']}: {report['events']} events, {report['rows_matched']} rows matched, "
        f"{report['rows_unmatched']} unmatched, {len(report['flagged_columns'])} of {report['columns_compared']} columns flagged",
    ]
    lines.extend(
        f"  {column['column']}: {column[MISMATCHED]} mismatched, {column[MISSING_AT_SERVING]} missing at serving, "
        f"{column[MISSING_IN_TRAINING]} missing in training (of {column['compared']})"
        for column in report["flagged_columns"]
    )
    return "\n".join(lines)
