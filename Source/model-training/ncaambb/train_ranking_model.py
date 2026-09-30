"""
NCAA MBB National Ranking (AP Top 25) model training, at team-poll
granularity rather than event-level or player-level. Reads
ranking_features.parquet (written by
Source/feature-engineering/ncaambb/build_dataset.py's build_ranking_dataset)
from S3, trains only on team-polls the AP Top 25 actually ranked
(label_current_rank not null), and runs the same multi-algorithm
candidate tournament via library.ml.backtest.run_backtest as every other
training script here.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_ranking_model.py
"""
import logging

from library.ml.sklearn_acceleration import patch_sklearn_if_available

# Must run before any sklearn import below.
patch_sklearn_if_available()

import pandas as pd

from library.ml import backtest, model_types, training_common
from library.ml import train_regressor_model_common as regressor_common

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ncaambb-train-model")

SPORT = "ncaambb"
MODEL_NAME = "national-ranking"
RANKING_FEATURES_KEY = "ncaambb/training-data/ranking_features.parquet"

# Identifiers/non-numeric columns, never model inputs. "season" is
# excluded since an absolute year doesn't generalize as a feature, same
# reasoning as NCAAFB's own version -- no "week" equivalent exists here
# to keep (see build_team_week_features's own docstring: this is
# poll-centric, not event/week-centric).
NON_FEATURE_COLUMNS = {"team_id", "as_of_date", "season"}
LABEL_COLUMN = "label_current_rank"

CANDIDATES = model_types.regressor_candidates(include_lightgbm=False)


def _filter_to_ranked_weeks(df: pd.DataFrame) -> pd.DataFrame:
    return df[df[LABEL_COLUMN].notna()].copy()


_job = training_common.ModelJob(
    globals(),
    trainer=regressor_common.train, sport=SPORT, model_name=MODEL_NAME, features_key=RANKING_FEATURES_KEY,
    row_noun="team-poll", label_column=LABEL_COLUMN, non_feature_columns=NON_FEATURE_COLUMNS, candidates=CANDIDATES,
    prepare=_filter_to_ranked_weeks, date_column="as_of_date",
)
_feature_columns = _job.feature_columns
train = _job.train
main = _job.main


if __name__ == "__main__":
    main()
