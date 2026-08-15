"""
NBA win-probability model training. Reads event_features.parquet (written
by Source/feature-engineering/nba/build_dataset.py) from S3, then runs
every CANDIDATES adapter as a competing candidate against the same
chronological holdout split via library.ml.backtest.run_backtest, and
promotes whichever wins on log_loss. See library/ml/backtest.py for the
tournament mechanics and library/ml/model_types.py for what each
candidate actually is. Same shape as
Source/model-training/nfl/train_win_probability_model.py -- see its own
docstring for the harness-level reasoning, identical here.

CANDIDATES includes LightGBMClassifierAdapter alongside the four NFL/
NCAAFB already use -- see library/ml/model_types.py's own docstring and
design/PROJECT_PLAN.md's Phase 3 model-selection section for why (NBA's
first real training run is also what produces the training_seconds data
that decides whether/how to trim a target's candidate list going
forward -- see library/ml/backtest.py's own comment on that field. No
trimming here yet, since there's no real duration data to trim from
until this runs for real).

Scheduled -- see the training orchestrator (sfn-training-orchestrator.tf,
driven by dynamodb-sport-registry.tf's nba_registry item) -- but every
bit as runnable manually via `aws ecs run-task`, same as
Source/feature-engineering/nba (see
Terraform/ecs-task-nba-train-win-probability-model.tf).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
"""
import logging
import os

try:
    # See NFL's own train_win_probability_model.py comment -- must run
    # before any sklearn import (including the one directly below).
    # XGBoost and LightGBM both have their own native optimization and
    # aren't affected either way.
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml.model_types import (
    LightGBMClassifierAdapter,
    LogisticRegressionAdapter,
    MLPClassifierAdapter,
    RandomForestClassifierAdapter,
    XGBoostClassifierAdapter,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nba-train-model")

SPORT = "nba"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "nba/training-data/event_features.parquet"

# Identifiers, never model inputs.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id"}
LABEL_COLUMN = "label_home_won"
SUMMARY_METRICS = ["accuracy", "log_loss", "naive_baseline_accuracy"]
PROMOTION_METRIC = "log_loss"

CANDIDATES = [
    XGBoostClassifierAdapter(),
    LogisticRegressionAdapter(),
    RandomForestClassifierAdapter(),
    MLPClassifierAdapter(),
    LightGBMClassifierAdapter(),
]


def _feature_columns(df):
    return training_common.feature_columns(df, NON_FEATURE_COLUMNS)


def train(s3: S3Manager, df) -> dict:
    """Runs the full candidate tournament and returns run_backtest's
    result ({"promotions": [card, ...], "candidates": [summary, ...]})."""
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

    # A trivial baseline -- always predict the home team wins, no model at
    # all. Its accuracy is just the fraction of home wins in the holdout
    # set. Computed once and shared across every candidate this run.
    naive_baseline_metrics = {"naive_baseline_accuracy": float(y_test.mean())}

    return backtest.run_backtest(
        s3, SPORT, MODEL_NAME, task="classification",
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
        run_id=training_common.resolve_run_id(),
    )


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, EVENT_FEATURES_KEY)
    df = training_common.load_features(s3, EVENT_FEATURES_KEY)
    logger.info("Loaded %d event rows", len(df))

    train(s3, df)


if __name__ == "__main__":
    main()
