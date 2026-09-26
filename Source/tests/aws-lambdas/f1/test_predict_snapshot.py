"""F1 is graded against one snapshot, taken on race day before there is a result."""
from datetime import datetime, timezone
from unittest.mock import patch

import event_prediction

EVENT_KEY = "SPORT#F1#EVENT#2026-15"


class _Table:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def query(self, key_condition, **kwargs):
        return list(self.rows)

    def put_item(self, item, condition_expression=None):
        exists = any(r["model_key"] == item["model_key"] for r in self.rows)
        if condition_expression is not None and exists:
            return False
        self.rows = [r for r in self.rows if r["model_key"] != item["model_key"]]
        self.rows.append(item)
        return True


class _Storage:
    def __init__(self, event):
        self.event = event

    def get_event(self, event_key):
        return self.event


def _row(model_key, value, generated_at):
    return {"event_key": EVENT_KEY, "model_key": model_key, "predicted_value": {"value": value}, "generated_at": generated_at}


def _compute(*rows):
    def fake(storage, s3, predictions_table, event_id):
        stamp = datetime.now(timezone.utc).isoformat()
        predictions_table.rows.extend(_row(key, value, stamp) for key, value in rows)
    return fake


def _run(event, table, *rows):
    with patch.object(event_prediction, "compute_and_cache_event", _compute(*rows)):
        return event_prediction.snapshot_event(_Storage(event), None, table, "2026-15")


def test_freezes_what_the_fresh_compute_recorded_as_the_final_pregame_snapshot():
    table = _Table([_row("MODEL#win-probability#v1#DRIVER#old", 0.1, "2020-01-01T00:00:00+00:00")])

    written = _run({"participants": []}, table, ("MODEL#win-probability#v2#DRIVER#max", 0.4), ("MODEL#constructor-win-probability#v1#CONSTRUCTOR#red_bull", 0.5))

    keys = {r["model_key"] for r in table.rows}
    assert "SNAPSHOT#final_pregame#MODEL#win-probability#v2#DRIVER#max" in keys
    assert "SNAPSHOT#final_pregame#MODEL#constructor-win-probability#v1#CONSTRUCTOR#red_bull" in keys
    assert not any("#old" in k and k.startswith("SNAPSHOT#") for k in keys)
    assert written == 2


def test_is_never_replaced_once_written():
    table = _Table([_row("SNAPSHOT#final_pregame#MODEL#win-probability#v2#DRIVER#max", 0.4, "t")])

    _run({"participants": []}, table, ("MODEL#win-probability#v2#DRIVER#max", 0.9))

    frozen = next(r for r in table.rows if r["model_key"].startswith("SNAPSHOT#"))
    assert frozen["predicted_value"]["value"] == 0.4


def test_does_nothing_once_the_race_has_a_result():
    table = _Table()
    event = {"participants": [{"entity_id": "max", "result": {"finish_position": 1}}]}

    assert _run(event, table, ("MODEL#win-probability#v2#DRIVER#max", 0.4)) == 0
    assert table.rows == []


def test_does_nothing_for_an_unknown_event():
    assert _run(None, _Table()) == 0
