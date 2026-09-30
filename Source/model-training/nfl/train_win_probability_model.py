"""
NFL win-probability model training. Reads event_features.parquet (written
by Source/feature-engineering/nfl/build_dataset.py) from S3, then runs
XGBoost, logistic regression, Random Forest, and an MLP as competing
candidates against the same chronological holdout split via
library.ml.backtest.run_backtest, and promotes whichever wins on
log_loss. Every retrain re-runs the full tournament, so the promoted
algorithm can change automatically as more data comes in.

Scheduled -- see Terraform/scheduler-nfl-train-win-probability-model.tf --
but also runnable manually via `aws ecs run-task`.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_win_probability_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

from library.ml import backtest, training_common
from library.ml import train_classifier_model_common as classifier_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

_job = classifier_common.WinProbabilityJob(
    globals(), SPORT, EVENT_FEATURES_KEY,
    extra_non_feature_columns=frozenset({"venue_city", "venue_state"}), include_lightgbm=False,
)
NON_FEATURE_COLUMNS = _job.non_feature_columns
LABEL_COLUMN = _job.label_column
CANDIDATES = _job.candidates
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
