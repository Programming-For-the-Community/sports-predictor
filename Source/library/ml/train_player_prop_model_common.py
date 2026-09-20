"""
Shared player-prop training logic (nfl/nba/ncaafb/ncaambb -- confirmed
identical shape across all 4 sports' train_player_prop_model.py before
sharing here). One model per TARGET_STAT, filtered to rows where the
player actually recorded it at meaningful volume (see
`_filter_to_target_stat` docstring below), regressing on their own
rolling stat history.

Each sport's own train_player_prop_model.py keeps `main()` and
`train(s3, df, target_stat)` (patches S3Manager/training_common/train by
attribute on ITS OWN module) and its own CANDIDATES/NON_FEATURE_COLUMNS/
threshold constants, calling into `train()`/`filter_to_target_stat()`
below via thin wrappers. Safe to share for the same reason as every other
`train_*_model_common.py` -- it only calls through `backtest`/
`training_common` as MODULE references. `feature_columns_fn` is a
required parameter rather than a shared implementation because
nfl/ncaafb additionally exclude the opposing side's stat categories
(offense vs. defense) while nba/ncaambb's flat stat_line has no such
distinction -- each sport passes its own.
"""
import json

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common

SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"


def model_name(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def filter_to_target_stat(
    df: pd.DataFrame, target_stat: str, *, label_column: str,
    min_prior_games_with_stat: int, min_avg_fraction_of_median: float,
) -> pd.DataFrame:
    """Filters to rows where the player recorded target_stat at
    meaningful volume. label_stat_line is JSON-encoded, so it's parsed
    here before filtering on it or turning it into a label; also applies
    the min-prior-games and min-avg-fraction-of-median thresholds."""
    stat_lines = df["label_stat_line"].apply(json.loads)
    has_stat = stat_lines.apply(lambda stat_line: target_stat in stat_line)
    filtered = df[has_stat].copy()
    filtered[label_column] = stat_lines[has_stat].apply(lambda stat_line: float(stat_line[target_stat]))

    volume_column = f"games_with_{target_stat}"
    filtered = filtered[filtered[volume_column] >= min_prior_games_with_stat]

    avg_column = f"avg_{target_stat}"
    median_avg = filtered[avg_column].median()
    filtered = filtered[filtered[avg_column] >= median_avg * min_avg_fraction_of_median]

    return filtered


def train(
    s3: S3Manager, df: pd.DataFrame, target_stat: str, *, sport: str, candidates: list,
    label_column: str, logger, feature_columns_fn,
) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]}).
    `df` is expected to already be filtered via `filter_to_target_stat`.
    feature_columns_fn(df) -> list[str] is the caller's own column
    selection (nfl/ncaafb additionally exclude the opposing side's stat
    categories; nba/ncaambb don't need to)."""
    feature_columns = feature_columns_fn(df)
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

    # A trivial baseline: predict this player's own rolling average
    # directly, no model.
    naive_predictions = test_df[f"avg_{target_stat}"]
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, sport, model_name(target_stat), task="regression",
        split=backtest.HoldoutSplit(X_train, y_train, X_test, y_test),
        candidates=candidates,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
            "target_stat": target_stat,
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "train_date_range": train_date_range,
            "test_date_range": test_date_range,
        },
        summary_metrics=SUMMARY_METRICS,
        promotion_metric=PROMOTION_METRIC,
        run_id=training_common.resolve_run_id(),
    )
