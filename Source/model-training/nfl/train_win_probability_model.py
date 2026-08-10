"""
NFL win-probability model training. Reads event_features.parquet (written
by Source/feature-engineering/nfl/build_dataset.py) from S3, then runs
XGBoost and logistic regression as competing candidates against the same
chronological holdout split via library.ml.backtest.run_backtest, and
promotes whichever wins on log_loss. See library/ml/backtest.py for the
tournament mechanics and library/ml/model_types.py for what each
candidate actually is.

This retires train_baseline_model.py's whole reason for existing:
logistic regression is now a REAL competing candidate in this same
scheduled run, able to actually win and reach production, not a separate,
never-scheduled script whose score a human had to eyeball. If the best
algorithm for this target changes as more seasons of data come in, a
future scheduled run can promote a different one automatically -- no
code change needed, since every retrain re-runs the full tournament.

Scheduled -- see Terraform/scheduler-nfl-train-win-probability-model.tf --
but every bit as runnable manually via `aws ecs run-task`, same as
Source/feature-engineering/nfl (see
Terraform/ecs-task-nfl-train-win-probability-model.tf).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
"""
import logging
import os

try:
    # Patches scikit-learn's LogisticRegression/ElasticNet/RandomForest
    # with Intel oneDAL-accelerated implementations -- must run before
    # library.ml.model_types (or anything else importing sklearn) so the
    # patched classes are what actually get instantiated. XGBoost has its
    # own native optimization and isn't affected either way. Training-only
    # (requirements.txt); never installed where library.ml.model_types is
    # imported for arm64 serving (Source/aws-lambdas/nfl/predict), which
    # this Intel-specific package doesn't support.
    from sklearnex import patch_sklearn
    patch_sklearn()
except ImportError:
    pass

from library.aws.s3_manager import S3Manager
from library.ml import backtest, training_common
from library.ml.model_types import (
    LogisticRegressionAdapter,
    MLPClassifierAdapter,
    RandomForestClassifierAdapter,
    XGBoostClassifierAdapter,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs. Every other label_* column is excluded
# generically below -- this target only predicts label_home_won, the
# score columns belong to the score-margin model (train_score_model.py),
# not this one.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_home_won"
SUMMARY_METRICS = ["accuracy", "log_loss", "naive_baseline_accuracy"]
PROMOTION_METRIC = "log_loss"

# The candidate pool for this target -- adding a genuinely new algorithm
# means adding one more adapter here, nothing else (see
# library/ml/model_types.py's own docstring). Four classification-capable
# algorithms: XGBoost (current production), logistic regression (a
# regularized linear baseline), Random Forest (bagged trees -- a
# different bias/variance tradeoff than XGBoost's boosting, less prone to
# overfitting a still-growing dataset), and a small MLP (the one
# candidate here that gets better, not worse, as the backfill grows
# toward its eventual 15-year size).
CANDIDATES = [
    XGBoostClassifierAdapter(),
    LogisticRegressionAdapter(),
    RandomForestClassifierAdapter(),
    MLPClassifierAdapter(),
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
    # all -- since home-field advantage alone is real signal. Its accuracy
    # is just the fraction of home wins in the holdout set: that naive
    # pick is right exactly when label_home_won is True. Computed once and
    # shared across every candidate this run, so they're all compared
    # against the same baseline.
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
