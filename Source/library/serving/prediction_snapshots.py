"""
Immutable pre-event prediction snapshots, kept alongside the live rows in the
same predictions table.

A live row's key is (event_key, MODEL#{name}#v{version}[#PLAYER#{id}]), so
every recompute overwrites the previous one -- by the time an event is graded,
the prediction that was current at kickoff may be long gone, or replaced by
one computed after the game began. A snapshot copies the freshly computed
rows to a separate key prefix:

    SNAPSHOT#{label}#MODEL#{name}#v{version}...

Existing readers all match on `MODEL#`, so they never see these rows. Grading
reads them through event_prediction_rows/pregame_rows below, which hand back
rows shaped exactly like live ones (snapshot prefix stripped).

FINAL_PREGAME is the only label: written by the prediction-scheduler shortly
before kickoff (team sports) or at the start of the event (PGA, F1) -- and, for
the field sports, never replaced once written.
"""
from boto3.dynamodb.conditions import Attr, Key

MODEL_PREFIX = "MODEL#"
SNAPSHOT_PREFIX = "SNAPSHOT#"
FINAL_PREGAME = "final_pregame"

SOURCE_SNAPSHOT = "snapshot"
SOURCE_LEGACY = "legacy"


def _label_prefix(label: str) -> str:
    return f"{SNAPSHOT_PREFIX}{label}#"


def snapshot_model_key(label: str, model_key: str) -> str:
    return f"{_label_prefix(label)}{model_key}"


def snapshot_event_predictions(
    predictions_table, event_key: str, label: str, generated_since: str, overwrite: bool = True,
) -> int:
    """Copies every live row for `event_key` generated at or after
    `generated_since` (an ISO timestamp taken just before the compute this
    snapshot follows) to its snapshot key. The cutoff is what keeps rows of
    an older model version, or a player scored earlier on demand, out of the
    snapshot. `overwrite=False` keeps the first snapshot written for a label
    (used where a label must stay frozen). Returns the number of rows written."""
    written = 0
    for row in predictions_table.query(Key("event_key").eq(event_key)):
        model_key = row.get("model_key", "")
        if not model_key.startswith(MODEL_PREFIX) or row.get("generated_at", "") < generated_since:
            continue
        item = {**row, "model_key": snapshot_model_key(label, model_key)}
        condition = None if overwrite else Attr("model_key").not_exists()
        if predictions_table.put_item(item, condition_expression=condition):
            written += 1
    return written


def has_snapshot(predictions_table, event_key: str, label: str = FINAL_PREGAME) -> bool:
    prefix = _label_prefix(label)
    rows = predictions_table.query(Key("event_key").eq(event_key) & Key("model_key").begins_with(prefix), limit=1)
    return bool(rows)


def pregame_rows(rows: list[dict], label: str = FINAL_PREGAME) -> tuple[list[dict], str]:
    """(rows, source). Snapshot rows for `label` when any exist, re-keyed to
    look like live `MODEL#...` rows so downstream readers need no changes;
    otherwise the live rows -- events predicted before snapshots existed --
    marked SOURCE_LEGACY so a scorecard can tell them apart."""
    prefix = _label_prefix(label)
    snapshot = [{**r, "model_key": r["model_key"][len(prefix):]} for r in rows if r.get("model_key", "").startswith(prefix)]
    if snapshot:
        return snapshot, SOURCE_SNAPSHOT
    return [r for r in rows if r.get("model_key", "").startswith(MODEL_PREFIX)], SOURCE_LEGACY


def event_prediction_rows(predictions_table, event_key: str, label: str = FINAL_PREGAME) -> list[dict]:
    """The rows to grade `event_key` against: its pre-kickoff snapshot when
    one exists, else its live rows (see pregame_rows)."""
    rows, _ = pregame_rows(predictions_table.query(Key("event_key").eq(event_key)), label)
    return rows
