"""
Shared regression training logic (F1's finish-position/qualifying/
sprint-grid and PGA's score/round/cutline -- confirmed identical shape
across all 6 scripts before sharing here, differing only in SPORT/
MODEL_NAME/LABEL_COLUMN/NON_FEATURE_COLUMNS/CANDIDATES and each script's
own pre-split row filter, which stays local since round/cutline's filters
are genuinely sport-specific).

Each script's own train_*.py keeps `main()` and `train(s3, df, ...)`
(patches S3Manager/training_common/train by attribute on ITS OWN module),
filters its own dataframe locally, then calls into `train()` below via a
thin wrapper. Safe to share for the same reason as
`train_classifier_model_common.py`/`train_score_model_common.py` -- it
only calls through `backtest`/`training_common` as MODULE references.
"""
import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common

SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"


def train(
    s3: S3Manager, df: pd.DataFrame, sport: str, model_name: str, *, label_column: str,
    non_feature_columns: set[str], candidates: list, logger, extra_metadata: dict | None = None,
) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]}).
    `df` is expected to already be filtered to the rows this model should
    train on -- any sport-specific pre-split filtering stays in the
    caller."""
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

    median = y_train.median()
    naive_predictions = pd.Series(median, index=y_test.index)
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    metadata = {
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_date_range": train_date_range,
        "test_date_range": test_date_range,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return backtest.run_backtest(
        s3, sport, model_name, task="regression",
        split=backtest.HoldoutSplit(X_train, y_train, X_test, y_test),
        candidates=candidates,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata=metadata,
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
        run_id=training_common.resolve_run_id(),
    )
