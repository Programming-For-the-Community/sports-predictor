"""
Shared player-prop training logic (nfl/nba/ncaafb/ncaambb -- confirmed
identical shape across all 4 sports' train_player_prop_model.py before
sharing here). One model per TARGET_STAT, filtered to rows where the
player actually recorded it at meaningful volume (see
`filter_to_target_stat` below), regressing on their own
rolling stat history.

PlayerPropJob is each sport's whole script; `train()` below only calls
through `backtest`/`training_common` as MODULE references, so a test
patching `train_X_model.backtest.run_backtest` patches the one shared
module every caller reads.
"""
import json
import os

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, model_types, training_common

SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

LABEL_COLUMN = "label_target_stat"
# Identifiers, never model inputs.
PLAYER_IDENTIFIER_COLUMNS = frozenset({"event_key", "player_key", "entity_id", "team_id", "opponent_id", "event_date"})
# A player must have recorded the target stat in at least this many of their
# own windowed prior games, not just the game being labeled -- guards
# against a one-off garbage-time stat line whose rolling avg_<stat> would
# otherwise be undefined/NaN.
MIN_PRIOR_GAMES_WITH_STAT = 2
# Excludes a player who clears MIN_PRIOR_GAMES_WITH_STAT at trivial volume.
MIN_AVG_FRACTION_OF_MEDIAN = 0.35
# A column surviving an all-null check with even one real value isn't enough.
MIN_NON_NULL_FRACTION = 0.05


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
        options=backtest.RunOptions(sample_weights=training_common.recency_weights(train_df["event_date"])),
    )


def _strip_metric_prefix(column: str) -> str:
    if column.startswith("avg_"):
        return column.removeprefix("avg_")
    if column.startswith("games_with_"):
        return column.removeprefix("games_with_")
    return column


class PlayerPropJob:
    """A sport's whole player-prop training script, one model per TARGET_STAT.
    `namespace` is the script module's globals() (see
    training_common.TrainingScript).

    A football sport passes its ESPN stat categories for each side of the
    ball: player_features.parquet has one schema across every position, so
    a QB's incidental defensive stat line can clear MIN_NON_NULL_FRACTION on
    its own -- feature_columns drops the side opposite the target stat's."""

    def __init__(
        self, namespace: dict, sport: str, features_key: str, *, include_lightgbm: bool = True,
        offensive_categories: frozenset[str] = frozenset(), defensive_categories: frozenset[str] = frozenset(),
    ) -> None:
        self.candidates = model_types.regressor_candidates(include_lightgbm=include_lightgbm)
        self.offensive_categories = offensive_categories
        self.defensive_categories = defensive_categories
        self._namespace = namespace
        self._sport = sport
        self._script = training_common.TrainingScript(
            namespace, features_key=features_key, row_noun="player-game",
            target=lambda: os.environ["TARGET_STAT"], model_name_for=model_name,
        )

    def filter_to_target_stat(self, df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
        return filter_to_target_stat(
            df, target_stat, label_column=LABEL_COLUMN,
            min_prior_games_with_stat=MIN_PRIOR_GAMES_WITH_STAT,
            min_avg_fraction_of_median=MIN_AVG_FRACTION_OF_MEDIAN,
        )

    def stat_category(self, stat_key: str) -> str | None:
        """The ESPN category a category-prefixed stat_line key belongs to --
        None for any category outside both sides (special teams, fumbles)."""
        for category in self.offensive_categories | self.defensive_categories:
            if stat_key == category or stat_key.startswith(f"{category}_"):
                return category
        return None

    def opposing_side_categories(self, target_stat: str) -> frozenset[str]:
        target_category = self.stat_category(target_stat)
        if target_category in self.offensive_categories:
            return self.defensive_categories
        if target_category in self.defensive_categories:
            return self.offensive_categories
        return frozenset()

    def feature_columns(self, df: pd.DataFrame, target_stat: str | None = None) -> list[str]:
        """Columns below MIN_NON_NULL_FRACTION are dropped -- after
        filter_to_target_stat narrows to one stat's population, most other
        positions' columns are structurally inapplicable -- and, given a
        target_stat, so is the opposing side of the ball."""
        candidates = training_common.feature_columns(df, PLAYER_IDENTIFIER_COLUMNS)
        minimum_non_null = len(df) * MIN_NON_NULL_FRACTION
        candidates = [col for col in candidates if df[col].notna().sum() >= minimum_non_null]
        if target_stat is None:
            return candidates
        opposing = self.opposing_side_categories(target_stat)
        return [col for col in candidates if self.stat_category(_strip_metric_prefix(col)) not in opposing]

    def train(self, s3: S3Manager, df: pd.DataFrame, target_stat: str) -> dict:
        df = self.filter_to_target_stat(df, target_stat)
        return train(
            s3, df, target_stat, sport=self._sport, candidates=self.candidates,
            label_column=LABEL_COLUMN, logger=self._namespace["logger"],
            feature_columns_fn=lambda frame: self.feature_columns(frame, target_stat),
        )

    def main(self) -> None:
        self._script.main()
