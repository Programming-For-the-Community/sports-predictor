"""
Shared game-score training logic (nfl/nba/ncaafb/ncaambb -- confirmed
byte-identical across all 4 team sports before sharing here). One model
per score target: SCORE_TARGET=margin/home_score/away_score, each
versioned independently.

Each sport's own train_score_model.py keeps `main()` (patches S3Manager/
training_common/train by attribute on ITS OWN module -- see each test
file's own patch.object calls) and its own `CANDIDATES`/
`NON_FEATURE_COLUMNS` config (the only two things that actually differ
between sports), and calls into `train()` below via a thin wrapper. Safe
to share `train()` itself (not just a shell) because it only calls through
`backtest`/`training_common` as MODULE references (`from library.ml import
backtest, training_common`), not individually-imported bare function
names -- `patch.object(train_score_model.backtest, "run_backtest", ...)`
mutates the one shared `library.ml.backtest` module object regardless of
which file's own `backtest.run_backtest(...)` call reads it, unlike
library.normalize.dispatch's own constraint (see that module's docstring
for the contrasting case where sharing was NOT safe).
"""
import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common

MODEL_NAMES = {
    "margin": "score-margin",
    "home_score": "home-score",
    "away_score": "away-score",
}
LABEL_COLUMN = "label_score_target"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"


def model_name(score_target: str) -> str:
    return MODEL_NAMES[score_target]


def add_label(df: pd.DataFrame, score_target: str) -> pd.DataFrame:
    df = df.copy()
    if score_target == "margin":
        df[LABEL_COLUMN] = df["label_home_score"] - df["label_away_score"]
    elif score_target == "home_score":
        df[LABEL_COLUMN] = df["label_home_score"]
    elif score_target == "away_score":
        df[LABEL_COLUMN] = df["label_away_score"]
    else:
        raise ValueError(f"Unknown SCORE_TARGET: {score_target!r} (expected margin, home_score, or away_score)")
    return df


def _column_or_mean(df: pd.DataFrame, column: str) -> pd.Series:
    """Fills a team with no rolling history yet with the column's own
    mean across these rows, rather than 0 (which would mean "predict a
    shutout")."""
    return df[column].fillna(df[column].mean())


def naive_prediction(df: pd.DataFrame, score_target: str) -> pd.Series:
    """A trivial baseline built from each team's own rolling
    scoring/allowing averages, no model: a team's scoring rate averaged
    with its opponent's allowing rate."""
    home_scored = _column_or_mean(df, "home_avg_points_scored")
    home_allowed = _column_or_mean(df, "home_avg_points_allowed")
    away_scored = _column_or_mean(df, "away_avg_points_scored")
    away_allowed = _column_or_mean(df, "away_avg_points_allowed")

    if score_target == "margin":
        return (home_scored - home_allowed) - (away_scored - away_allowed)
    if score_target == "home_score":
        return (home_scored + away_allowed) / 2
    return (away_scored + home_allowed) / 2  # away_score


def train(
    s3: S3Manager, df: pd.DataFrame, score_target: str, *, sport: str, candidates: list,
    non_feature_columns: set[str], logger,
) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
    df = add_label(df, score_target)
    feature_columns = training_common.feature_columns(df, non_feature_columns)
    train_df, test_df = training_common.chronological_split(df, training_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = training_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[LABEL_COLUMN]
    X_test = training_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[LABEL_COLUMN]

    naive_predictions = naive_prediction(test_df, score_target)
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, sport, model_name(score_target), task="regression",
        X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        candidates=candidates,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "score_target": score_target,
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "train_date_range": train_date_range,
            "test_date_range": test_date_range,
        },
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
        run_id=training_common.resolve_run_id(),
    )
