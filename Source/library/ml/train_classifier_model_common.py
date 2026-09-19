"""
Shared binary-classification training logic (F1's podium/winprob/dnf/
sprint-podium/sprint-winprob/constructor-winprob and PGA's top10/top5/
match-winprob/cup-winprob -- confirmed identical shape across all 10
scripts before sharing here, differing only in SPORT/MODEL_NAME/
LABEL_COLUMN/NON_FEATURE_COLUMNS/CANDIDATES and whether the label needs a
pre-split notna filter + int coercion).

Each script's own train_*.py keeps `main()` and `train(s3, df)` (patches
S3Manager/training_common/train by attribute on ITS OWN module -- see each
test file's own patch.object calls) and its own CANDIDATES/
NON_FEATURE_COLUMNS/LABEL_COLUMN config, calling into `train()` below via a
thin wrapper. Safe to share `train()` itself because it only calls through
`backtest`/`training_common` as MODULE references, not individually-
imported bare function names -- patching `train_X_model.backtest.
run_backtest` mutates the one shared `library.ml.backtest` module object
regardless of which file's own `backtest.run_backtest(...)` call reads it
(same precedent as `train_score_model_common.py`).
"""
from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common

SUMMARY_METRICS = ["accuracy", "log_loss", "naive_baseline_accuracy"]
PROMOTION_METRIC = "log_loss"


def train(
    s3: S3Manager, df, sport: str, model_name: str, *, label_column: str,
    non_feature_columns: set[str], candidates: list, logger,
    drop_null_label: bool = False, coerce_int_label: bool = False,
) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    if drop_null_label:
        df = df[df[label_column].notna()]

    feature_columns = training_common.feature_columns(df, non_feature_columns)
    train_df, test_df = training_common.chronological_split(df, training_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = training_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[label_column]
    X_test = training_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[label_column]
    if coerce_int_label:
        y_train = y_train.astype(int)
        y_test = y_test.astype(int)

    naive_baseline_accuracy = float(max(y_test.mean(), 1 - y_test.mean()))
    naive_baseline_metrics = {"naive_baseline_accuracy": naive_baseline_accuracy}

    return backtest.run_backtest(
        s3, sport, model_name, task="classification",
        split=backtest.HoldoutSplit(X_train, y_train, X_test, y_test),
        candidates=candidates,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "train_date_range": train_date_range,
            "test_date_range": test_date_range,
        },
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
        run_id=training_common.resolve_run_id(),
    )
