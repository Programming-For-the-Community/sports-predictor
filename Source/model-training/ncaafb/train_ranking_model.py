"""
NCAAFB National Ranking (1-25) model training -- a genuinely new model
shape for this project, team-week granularity rather than event-level or
player-level (see library.features.ncaafb.build_team_week_features' own
docstring). Reads ranking_features.parquet (written by
Source/feature-engineering/ncaafb/build_dataset.py's build_ranking_dataset)
from S3, trains only on team-weeks CFBD's own AP Top 25 poll actually
ranked (label_current_rank not null -- an unranked team-week has no
well-defined numeric label, same "missing, not fabricated" discipline
this project applies everywhere else), and runs the same multi-algorithm
candidate tournament via library.ml.backtest.run_backtest as every other
training script here.

At serving time (a later phase), every FBS team's predicted rank is
computed and sorted; the 25 lowest predicted values become the projected
Top 25 -- no separate is-ranked classifier, same "let the model's own
relative ordering define membership" approach the win/margin models
already use.

Its own image (Terraform/ecs-task-ncaafb-train-ranking-model.tf), not
train_win_probability_model.py's -- see that file's own comment for why.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_ranking_model.py
"""
import logging
import os

try:
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml.model_types import ElasticNetAdapter, MLPRegressorAdapter, RandomForestRegressorAdapter, XGBoostRegressorAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaafb-train-model")

SPORT = "ncaafb"
MODEL_NAME = "national-ranking"
RANKING_FEATURES_KEY = "ncaafb/training-data/ranking_features.parquet"

# Identifiers/non-numeric columns, never model inputs. "conference" and
# "season_type" are raw strings -- excluded the same way NFL's own
# event-level script excludes venue_city/venue_state (see
# train_win_probability_model.py's NON_FEATURE_COLUMNS comment); "season"
# is excluded too since an absolute year doesn't generalize as a feature.
# "week" stays IN as a feature -- it's numeric and meaningfully captures
# how far into the season this team-week snapshot falls. label_
# current_rank is excluded automatically by training_common.feature_
# columns' own label_ prefix rule, same as every other script here.
NON_FEATURE_COLUMNS = {"event_key", "team_id", "event_date", "season", "season_type", "conference"}
LABEL_COLUMN = "label_current_rank"
SUMMARY_METRICS = ["rmse", "mae", "naive_baseline_rmse", "naive_baseline_mae"]
PROMOTION_METRIC = "rmse"

CANDIDATES = [
    XGBoostRegressorAdapter(),
    ElasticNetAdapter(),
    RandomForestRegressorAdapter(),
    MLPRegressorAdapter(),
]


def _filter_to_ranked_weeks(df: pd.DataFrame) -> pd.DataFrame:
    return df[df[LABEL_COLUMN].notna()].copy()


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df: pd.DataFrame) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"winner": card, "promoted": bool, "candidates": [card, ...]})."""
    df = _filter_to_ranked_weeks(df)
    feature_columns = _feature_columns(df)
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

    # A trivial baseline -- predict the training set's own median rank
    # for every row, no model at all, since the AP Top 25 has no
    # "obviously right" home-field-style prior to lean on.
    median_rank = y_train.median()
    naive_predictions = pd.Series(median_rank, index=y_test.index)
    naive_baseline_metrics = {
        "naive_baseline_rmse": float(root_mean_squared_error(y_test, naive_predictions)),
        "naive_baseline_mae": float(mean_absolute_error(y_test, naive_predictions)),
    }

    return backtest.run_backtest(
        s3, SPORT, MODEL_NAME, task="regression",
        X_train=X_train, y_train=y_train, X_test=X_test, y_test=y_test,
        candidates=CANDIDATES,
        naive_baseline_metrics=naive_baseline_metrics,
        extra_metadata={
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
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, RANKING_FEATURES_KEY)
    df = training_common.load_features(s3, RANKING_FEATURES_KEY)
    logger.info("Loaded %d team-week rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
