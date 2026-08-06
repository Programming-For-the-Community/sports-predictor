"""
NFL player-prop model training -- one model per target stat (e.g.
TARGET_STAT=passing_yards for QB passing yards, TARGET_STAT=rushing_yards
for RB rushing yards, TARGET_STAT=receptions for a receiver). Reads
player_features.parquet (written by
Source/feature-engineering/nfl/build_dataset.py's build_player_dataset)
from S3, filters to rows where the player actually recorded TARGET_STAT
in the game being labeled AND has an established history of it (see
_filter_to_target_stat), and trains a regressor predicting that value
from their own rolling stat history (see build_player_features and
rolling_player_stat_averages in library/features/nfl.py). Requires a
player_features.parquet built after games_with_<stat> was added to
rolling_player_stat_averages -- re-run feature engineering first if an
older dataset is all that's in S3.

Deliberately one script for every stat rather than one script (or one
Terraform task definition, or one image) per stat -- TARGET_STAT is the
only thing that varies between a passing-yards run and a rushing-yards
run; the dataset, split, evaluation, artifact writing, and promotion are
all identical, and live in library/ml/training_common.py and
library/ml/backtest.py alongside train_win_probability_model.py and train_score_model.py.
Run a given stat by overriding the TARGET_STAT environment variable at
`aws ecs run-task` time (see
Terraform/ecs-task-nfl-train-player-prop-model.tf) rather than deploying
a new task definition per stat.

Runs every CANDIDATES adapter as a competing candidate against the same
holdout split via library.ml.backtest.run_backtest, and promotes
whichever wins on rmse -- see train_win_probability_model.py's own docstring for the
reasoning behind this roster, which mirrors it (same four algorithms,
this target's regression-capable candidates instead of win-probability's
classification-capable ones).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    TARGET_STAT (a stat_line key, e.g. "passing_yards", "rushing_yards")
    AWS_REGION

Usage:
    TARGET_STAT=passing_yards python train_player_prop_model.py
"""
import json
import logging
import os

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
PLAYER_FEATURES_KEY = "nfl/training-data/player_features.parquet"

# Identifiers, never model inputs. label_stat_line and label_started are
# excluded generically (they start with "label_"), same as every other
# script here -- LABEL_COLUMN below also starts with "label_" for the
# same reason, so it never leaks into feature_columns either.
NON_FEATURE_COLUMNS = {"event_key", "player_key", "entity_id", "team_id", "opponent_id", "event_date"}
LABEL_COLUMN = "label_target_stat"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]

# A player must have recorded TARGET_STAT in at least this many of their
# own windowed prior games, not just the game being labeled -- excludes
# one-off gadget plays (e.g. a WR's single career pass attempt) whose
# rolling avg_<stat> would otherwise be undefined/NaN.
# MIN_AVG_FRACTION_OF_MEDIAN below catches a player who clears this count
# at trivial volume, so this stays a low bar.
MIN_PRIOR_GAMES_WITH_STAT = 2

# A player can pass the games_with_<stat> bar above by recurring at low
# volume rather than one-off (e.g. a WR running a wildcat package in a
# couple of recent games) -- their avg_<stat> stays far below a real
# participant's. Scale-invariant relative to the stat's own filtered
# population median rather than an absolute floor, so the same fraction
# applies across differently-scaled stats (yards vs. sacks) without
# per-stat calibration.
MIN_AVG_FRACTION_OF_MEDIAN = 0.35

# A column surviving an all-null check with even one real value isn't
# enough -- a handful of anomalous rows can still give a shallow tree an
# irrelevant column to exploit. Requiring a real fraction of rows to have
# a value, not just one, keeps genuinely common cross-category signal
# (e.g. avg_rushing_yards for dual-threat QBs) while excluding columns
# whose few non-null values come from anomalous rows.
MIN_NON_NULL_FRACTION = 0.05

# ESPN category names (see boxscore_to_player_game_stats in
# library/normalize/espn.py) grouped by which side of the ball they
# belong to. "interceptions" is the defensive-picks category (bare
# "interceptions" key) -- distinct from "passing"'s own "interceptions"
# key, already disambiguated to "passing_interceptions" by that
# function's category-prefixing rule. "fumbles" is deliberately left out
# of both -- ball carriers AND defenders forcing/recovering fumbles are
# both real, so it isn't cleanly one side's stat; MIN_NON_NULL_FRACTION
# decides its fate on prevalence instead.
OFFENSIVE_CATEGORIES = {"passing", "rushing", "receiving"}
DEFENSIVE_CATEGORIES = {"defensive", "interceptions"}


def _model_name(target_stat: str) -> str:
    return f"player-prop-{target_stat.replace('_', '-')}"


def _filter_to_target_stat(df: pd.DataFrame, target_stat: str) -> pd.DataFrame:
    """Not every player-game recorded target_stat (a kicker never has
    passing_yards) -- label_stat_line is JSON-encoded (see
    build_player_features in library/features/nfl.py), so it has to be
    parsed before it can be filtered on or turned into a label.

    Also requires MIN_PRIOR_GAMES_WITH_STAT prior games with this stat, via
    the games_with_<stat> column (rolling_player_stat_averages in
    library/features/nfl.py) -- without this, a one-off gadget-play row
    (e.g. a WR's single career pass attempt) still passes the has_stat
    check above, and its avg_<other-position>_* features give a shallow
    tree an easy way to isolate that single fluke label.

    Also requires avg_<stat> to be at least MIN_AVG_FRACTION_OF_MEDIAN of
    the remaining population's own median avg_<stat> (see
    MIN_AVG_FRACTION_OF_MEDIAN). The median is computed after the
    games_with_<stat> filter above, not before, so the flukes that filter
    excludes don't drag the median down first.
    """
    stat_lines = df["label_stat_line"].apply(json.loads)
    has_stat = stat_lines.apply(lambda stat_line: target_stat in stat_line)
    filtered = df[has_stat].copy()
    filtered[LABEL_COLUMN] = stat_lines[has_stat].apply(lambda stat_line: float(stat_line[target_stat]))

    volume_column = f"games_with_{target_stat}"
    filtered = filtered[filtered[volume_column] >= MIN_PRIOR_GAMES_WITH_STAT]

    avg_column = f"avg_{target_stat}"
    median_avg = filtered[avg_column].median()
    filtered = filtered[filtered[avg_column] >= median_avg * MIN_AVG_FRACTION_OF_MEDIAN]

    return filtered


def _stat_category(stat_key: str) -> str | None:
    """The ESPN category a stat_line key belongs to, inferred from its own
    name -- every key already follows the category-prefixed convention
    (see boxscore_to_player_game_stats), so this needs no separate
    mapping. None for a category not in OFFENSIVE_CATEGORIES/
    DEFENSIVE_CATEGORIES (special teams, fumbles) -- those are left to
    MIN_NON_NULL_FRACTION rather than a hard side-of-the-ball rule."""
    for category in OFFENSIVE_CATEGORIES | DEFENSIVE_CATEGORIES:
        if stat_key == category or stat_key.startswith(f"{category}_"):
            return category
    return None


def _opposing_side_categories(target_stat: str) -> set[str]:
    target_category = _stat_category(target_stat)
    if target_category in OFFENSIVE_CATEGORIES:
        return DEFENSIVE_CATEGORIES
    if target_category in DEFENSIVE_CATEGORIES:
        return OFFENSIVE_CATEGORIES
    return set()


def _strip_metric_prefix(column: str) -> str:
    if column.startswith("avg_"):
        return column.removeprefix("avg_")
    if column.startswith("games_with_"):
        return column.removeprefix("games_with_")
    return column


def _feature_columns(df: pd.DataFrame, target_stat: str) -> list[str]:
    """player_features.parquet has one schema spanning every position in
    the league (build_dataset.py's _write_parquet unions every row's
    columns) -- after _filter_to_target_stat narrows to one stat's
    population, most other positions' columns (kicking, punting,
    defensive, ...) are structurally inapplicable to every remaining row.
    Dropping columns below MIN_NON_NULL_FRACTION here, scoped to this
    script rather than library.ml.training_common.feature_columns, keeps
    the other two scripts' feature schema stable regardless of a given
    week's null pattern.

    Also excludes the opposing side of the ball outright (see
    OFFENSIVE_CATEGORIES/DEFENSIVE_CATEGORIES) -- MIN_NON_NULL_FRACTION
    alone wouldn't catch this, since a QB's indirect defensive stat line
    (e.g. tackling after an interception) is common enough to clear that
    bar on its own."""
    candidates = training_common.feature_columns(df, NON_FEATURE_COLUMNS)
    minimum_non_null = len(df) * MIN_NON_NULL_FRACTION
    candidates = [col for col in candidates if df[col].notna().sum() >= minimum_non_null]

    opposing_categories = _opposing_side_categories(target_stat)
    return [col for col in candidates if _stat_category(_strip_metric_prefix(col)) not in opposing_categories]


def train(s3: S3Manager, df: pd.DataFrame, target_stat: str) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"winner": card, "promoted": bool, "candidates": [card, ...]})."""
    df = _filter_to_target_stat(df, target_stat)
    feature_columns = _feature_columns(df, target_stat)
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

    # A trivial baseline -- predict this player's own rolling average
    # directly, no model at all -- since that average is already one of
    # the model's own input features. If a candidate doesn't clear this
    # by much, it isn't adding real value beyond "assume this game looks
    # like their recent history."
    naive_predictions = test_df[f"avg_{target_stat}"]
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, SPORT, _model_name(target_stat), task="regression",
        X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        candidates=CANDIDATES,
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
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    target_stat = os.environ["TARGET_STAT"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)
    model_name = _model_name(target_stat)

    logger.info("Loading %s training data from s3://%s/%s", model_name, bucket, PLAYER_FEATURES_KEY)
    df = training_common.load_features(s3, PLAYER_FEATURES_KEY)
    logger.info("Loaded %d player-game rows", len(df))

    train(s3, df, target_stat)


if __name__ == "__main__":
    main()
