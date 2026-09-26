"""
PGA is graded against ONE snapshot, taken at the start of the tournament
(before any round is played). It holds the tournament-level forecasts and every
round's forecast, and the served round breakdown prefers it over whatever a
later on-demand compute left behind.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import event_prediction

EVENT_KEY = "SPORT#PGA#EVENT#999"


class _Table:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def query(self, key_condition, **kwargs):
        return list(self.rows)

    def put_item(self, item, condition_expression=None):
        exists = any(r["event_key"] == item["event_key"] and r["model_key"] == item["model_key"] for r in self.rows)
        if condition_expression is not None and exists:
            return False
        self.rows = [r for r in self.rows if not (r["event_key"] == item["event_key"] and r["model_key"] == item["model_key"])]
        self.rows.append(item)
        return True


class _Storage:
    def __init__(self, event):
        self.event = event

    def get_event(self, event_key):
        return self.event


def _row(model_key, value, generated_at="2026-09-17T11:00:00+00:00"):
    return {"event_key": EVENT_KEY, "model_key": model_key, "predicted_value": {"value": value}, "generated_at": generated_at}


def _event(played_rounds=0, event_type="field"):
    rounds = [{"round": n, "score_to_par": -3, "total_strokes": 69} for n in range(1, played_rounds + 1)]
    return {
        "event_key": EVENT_KEY, "event_id": "999", "event_type": event_type,
        "participants": [
            {"entity_id": "1", "result": {"status": "in_progress", "rounds": rounds}},
            {"entity_id": "2", "result": {"status": "in_progress", "rounds": rounds}},
        ],
    }


def _compute_records(*rows):
    """A fake compute that records the rows a real one would."""
    def fake(storage, s3, predictions_table, event_id):
        stamp = datetime.now(timezone.utc).isoformat()
        for model_key, value in rows:
            predictions_table.rows.append(_row(model_key, value, stamp))
    return fake


class TestHistoricalRoundPredictions:
    def test_the_start_of_tournament_snapshot_beats_a_later_live_row(self):
        table = _Table([
            _row("MODEL#round-2#v3#GOLFER#1", 0.5),  # left by a later on-demand compute
            _row("SNAPSHOT#final_pregame#MODEL#round-2#v3#GOLFER#1", -1.5),
        ])

        result = event_prediction._historical_round_predictions(table, EVENT_KEY)

        assert result["1"][2] == {"value": -1.5, "model_version": 3}

    def test_falls_back_to_the_live_row_when_no_snapshot_exists(self):
        table = _Table([_row("MODEL#round-1#v2#GOLFER#1", -2.0)])

        assert event_prediction._historical_round_predictions(table, EVENT_KEY)["1"][1] == {"value": -2.0, "model_version": 2}

    def test_every_rounds_forecast_comes_from_the_one_snapshot(self):
        table = _Table([
            _row("SNAPSHOT#final_pregame#MODEL#round-1#v3#GOLFER#1", -1.0),
            _row("SNAPSHOT#final_pregame#MODEL#round-2#v3#GOLFER#1", -2.0),
            _row("SNAPSHOT#final_pregame#MODEL#round-2#v3#GOLFER#2", -3.0),
            _row("SNAPSHOT#final_pregame#MODEL#top-10-probability#v2#GOLFER#1", 0.4),  # not a round model
        ])

        result = event_prediction._historical_round_predictions(table, EVENT_KEY)

        assert result["1"] == {1: {"value": -1.0, "model_version": 3}, 2: {"value": -2.0, "model_version": 3}}
        assert result["2"] == {2: {"value": -3.0, "model_version": 3}}


class TestSnapshotEvent:
    def _run(self, event, table, *rows):
        with patch.object(event_prediction, "compute_and_cache_event", _compute_records(*rows)):
            return event_prediction.snapshot_event(_Storage(event), None, table, "999")

    def test_before_any_round_it_freezes_everything_as_the_final_pregame_snapshot(self):
        table = _Table()

        written = self._run(
            _event(), table,
            ("MODEL#round-1#v3#GOLFER#1", -2.0), ("MODEL#round-4#v3#GOLFER#1", -1.0), ("MODEL#top-10-probability#v2#GOLFER#1", 0.4),
        )

        keys = {r["model_key"] for r in table.rows}
        assert "SNAPSHOT#final_pregame#MODEL#round-1#v3#GOLFER#1" in keys
        assert "SNAPSHOT#final_pregame#MODEL#round-4#v3#GOLFER#1" in keys
        assert "SNAPSHOT#final_pregame#MODEL#top-10-probability#v2#GOLFER#1" in keys
        assert written == 3

    def test_it_is_never_replaced_once_written(self):
        table = _Table([_row("SNAPSHOT#final_pregame#MODEL#round-1#v3#GOLFER#1", -1.0)])

        self._run(_event(), table, ("MODEL#round-1#v3#GOLFER#1", 5.0))

        frozen = next(r for r in table.rows if r["model_key"].startswith("SNAPSHOT#"))
        assert frozen["predicted_value"]["value"] == -1.0

    def test_only_a_final_pregame_label_is_ever_written(self):
        table = _Table()

        self._run(_event(), table, ("MODEL#round-1#v3#GOLFER#1", -2.0))

        assert {r["model_key"].split("#MODEL#")[0] for r in table.rows if r["model_key"].startswith("SNAPSHOT#")} == {"SNAPSHOT#final_pregame"}

    def test_once_a_round_has_been_played_it_is_too_late_and_writes_nothing(self):
        table = _Table()

        assert self._run(_event(played_rounds=1), table, ("MODEL#round-2#v3#GOLFER#1", -1.0)) == 0
        assert table.rows == []

    def test_does_nothing_for_a_match_play_event(self):
        table = _Table()

        assert self._run(_event(event_type="match_play"), table, ("MODEL#match-win-probability#v1#MATCH", 0.5)) == 0
        assert table.rows == []
