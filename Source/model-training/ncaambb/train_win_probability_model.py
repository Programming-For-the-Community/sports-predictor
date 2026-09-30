"""
NCAA MBB win-probability model training. Reads event_features.parquet
(written by Source/feature-engineering/ncaambb/build_dataset.py) from S3,
then runs every CANDIDATES adapter as a competing candidate against the
same chronological holdout split via library.ml.backtest.run_backtest,
and promotes whichever wins on log_loss. See library/ml/backtest.py for
the tournament mechanics and library/ml/model_types.py for what each
candidate actually is.

Scheduled -- see the training orchestrator (sfn-training-orchestrator.tf,
driven by dynamodb-sport-registry.tf's ncaambb_registry item) -- but also
runnable manually via `aws ecs run-task`.

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
logger = logging.getLogger("ncaambb-train-model")

SPORT = "ncaambb"
EVENT_FEATURES_KEY = "ncaambb/training-data/event_features.parquet"

_job = classifier_common.WinProbabilityJob(globals(), SPORT, EVENT_FEATURES_KEY)
NON_FEATURE_COLUMNS = _job.non_feature_columns
LABEL_COLUMN = _job.label_column
CANDIDATES = _job.candidates
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
