"""snapshot_event: one fresh compute, then a copy of exactly the rows that
compute recorded to the event's final-pregame snapshot."""
from datetime import datetime, timezone
from unittest.mock import patch

import event_prediction

EVENT_KEY = "SPORT#NFL#EVENT#1"


class _Table:
    def __init__(self, rows):
        self.rows = list(rows)

    def query(self, key_condition, **kwargs):
        return list(self.rows)

    def put_item(self, item, condition_expression=None):
        self.rows.append(item)
        return True


def _row(model_key, generated_at):
    return {"event_key": EVENT_KEY, "model_key": model_key, "generated_at": generated_at, "predicted_value": {"value": 0.6}}


def test_snapshots_only_rows_recorded_by_this_compute():
    table = _Table([_row("MODEL#win-probability#v8", "2020-01-01T00:00:00+00:00")])

    def fake_compute(storage, s3, predictions_table, event_id, sport, predict_event_fn):
        predictions_table.rows.append(_row("MODEL#win-probability#v9", datetime.now(timezone.utc).isoformat()))

    with patch.object(event_prediction.common, "compute_and_cache_event", fake_compute):
        written = event_prediction.snapshot_event(None, None, table, "1")

    assert written == 1
    assert [r["model_key"] for r in table.rows if r["model_key"].startswith("SNAPSHOT#")] == [
        "SNAPSHOT#final_pregame#MODEL#win-probability#v9",
    ]


def test_writes_nothing_when_the_compute_recorded_nothing():
    table = _Table([])

    with patch.object(event_prediction.common, "compute_and_cache_event", lambda *a, **k: None):
        assert event_prediction.snapshot_event(None, None, table, "1") == 0
    assert table.rows == []
