from boto3.dynamodb.conditions import Key

from library.serving import prediction_snapshots as snapshots


def _matches(condition, item: dict) -> bool:
    expression = condition.get_expression()
    operator = expression["operator"]
    values = expression["values"]
    if operator == "AND":
        return all(_matches(v, item) for v in values)
    attribute = values[0].name
    if operator == "=":
        return item.get(attribute) == values[1]
    if operator == "begins_with":
        return str(item.get(attribute, "")).startswith(values[1])
    raise AssertionError(f"unsupported operator {operator}")


class FakeTable:
    def __init__(self, rows: list[dict]):
        self.rows = list(rows)

    def query(self, key_condition, index_name=None, scan_index_forward=True, limit=None):
        matches = [r for r in self.rows if _matches(key_condition, r)]
        return matches[:limit] if limit else matches

    def put_item(self, item, condition_expression=None):
        if condition_expression is not None and any(
            r["event_key"] == item["event_key"] and r["model_key"] == item["model_key"] for r in self.rows
        ):
            return False
        self.rows.append(item)
        return True


def _row(model_key, generated_at, value=0.6, event_key="E1"):
    return {"event_key": event_key, "model_key": model_key, "generated_at": generated_at, "predicted_value": {"value": value}}


class TestSnapshotEventPredictions:
    def test_copies_only_rows_generated_since_the_cutoff(self):
        table = FakeTable([
            _row("MODEL#win-probability#v8", "2026-09-27T16:00:00Z"),  # older version, before this compute
            _row("MODEL#win-probability#v9", "2026-09-27T16:46:00Z"),
            _row("MODEL#score-margin#v9", "2026-09-27T16:46:01Z"),
        ])

        written = snapshots.snapshot_event_predictions(table, "E1", snapshots.FINAL_PREGAME, "2026-09-27T16:45:00Z")

        assert written == 2
        keys = {r["model_key"] for r in table.rows}
        assert "SNAPSHOT#final_pregame#MODEL#win-probability#v9" in keys
        assert "SNAPSHOT#final_pregame#MODEL#score-margin#v9" in keys
        assert "SNAPSHOT#final_pregame#MODEL#win-probability#v8" not in keys

    def test_never_snapshots_a_snapshot_row(self):
        table = FakeTable([_row("SNAPSHOT#final_pregame#MODEL#win-probability#v9", "2026-09-27T16:46:00Z")])

        assert snapshots.snapshot_event_predictions(table, "E1", snapshots.FINAL_PREGAME, "2026-09-27T16:45:00Z") == 0

    def test_overwrite_false_keeps_the_first_snapshot(self):
        table = FakeTable([_row("MODEL#round-1#v2", "2026-09-27T16:46:00Z", value=1.0)])
        snapshots.snapshot_event_predictions(table, "E1", "round_1", "2026-09-27T16:45:00Z", overwrite=False)
        table.rows[0] = _row("MODEL#round-1#v2", "2026-09-27T17:46:00Z", value=2.0)

        written = snapshots.snapshot_event_predictions(table, "E1", "round_1", "2026-09-27T16:45:00Z", overwrite=False)

        assert written == 0
        frozen = next(r for r in table.rows if r["model_key"].startswith("SNAPSHOT#round_1#"))
        assert frozen["predicted_value"]["value"] == 1.0

    def test_overwrite_true_replaces_an_earlier_snapshot(self):
        table = FakeTable([_row("MODEL#win-probability#v9", "2026-09-27T16:46:00Z", value=0.7)])
        table.rows.append(_row("SNAPSHOT#final_pregame#MODEL#win-probability#v9", "2026-09-27T16:10:00Z", value=0.5))

        written = snapshots.snapshot_event_predictions(table, "E1", snapshots.FINAL_PREGAME, "2026-09-27T16:45:00Z")

        assert written == 1


class TestHasSnapshot:
    def test_true_only_for_that_label(self):
        table = FakeTable([_row("SNAPSHOT#final_pregame#MODEL#win-probability#v9", "t")])

        assert snapshots.has_snapshot(table, "E1", snapshots.FINAL_PREGAME) is True
        assert snapshots.has_snapshot(table, "E1", "round_1") is False
        assert snapshots.has_snapshot(table, "OTHER", snapshots.FINAL_PREGAME) is False


class TestPregameRows:
    def test_prefers_snapshot_rows_rekeyed_like_live_rows(self):
        rows = [
            _row("MODEL#win-probability#v9", "2026-09-27T20:00:00Z", value=0.9),  # recomputed after kickoff
            _row("SNAPSHOT#final_pregame#MODEL#win-probability#v9", "2026-09-27T16:46:00Z", value=0.6),
        ]

        result, source = snapshots.pregame_rows(rows)

        assert source == snapshots.SOURCE_SNAPSHOT
        assert result == [_row("MODEL#win-probability#v9", "2026-09-27T16:46:00Z", value=0.6)]

    def test_falls_back_to_live_rows_marked_legacy(self):
        rows = [_row("MODEL#win-probability#v9", "2026-09-27T16:00:00Z")]

        result, source = snapshots.pregame_rows(rows)

        assert source == snapshots.SOURCE_LEGACY
        assert result == rows

    def test_no_rows_at_all_is_legacy_and_empty(self):
        assert snapshots.pregame_rows([]) == ([], snapshots.SOURCE_LEGACY)


def test_event_prediction_rows_queries_and_prefers_snapshot():
    table = FakeTable([
        _row("MODEL#win-probability#v9", "2026-09-27T20:00:00Z", value=0.9),
        _row("SNAPSHOT#final_pregame#MODEL#win-probability#v9", "2026-09-27T16:46:00Z", value=0.6),
        _row("MODEL#win-probability#v9", "t", value=0.1, event_key="E2"),
    ])

    rows = snapshots.event_prediction_rows(table, "E1")

    assert [r["predicted_value"]["value"] for r in rows] == [0.6]
