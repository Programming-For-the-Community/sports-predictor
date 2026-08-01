"""
NFL win-probability model training (XGBoost). Reads event_features.parquet
(written by Source/feature-engineering/nfl/build_dataset.py) from S3,
searches XGBoost hyperparameters (see _tune_hyperparameters), trains the
final binary classifier predicting label_home_won, and writes a versioned
model artifact plus a model card (training window, row counts,
hyperparameters, and holdout metrics -- everything needed to know what a
given version actually is without re-running or cross-referencing logs)
to S3. Shared plumbing (loading features, chronological split, dtype
coercion, holdout evaluation, versioned artifact writing) lives in
model_common.py -- see train_baseline_model.py for the logistic
regression comparison baseline that shares it.

Scheduled -- see Terraform/scheduler-nfl-train-model.tf -- but every bit
as runnable manually via `aws ecs run-task`, same as
Source/feature-engineering/nfl (see Terraform/ecs-task-nfl-train-model.tf).
Each run trains a brand-new version under its own prefix; it never
overwrites a previous one -- see library.storage.model_artifacts for the
versioning scheme, and Terraform/s3-model-artifacts.tf for why versioning
is per-model rather than one shared counter across every model this sport
has. After writing the artifact, model_common.promote_if_better decides
whether this version becomes the one an eventual inference Lambda would
read -- see its docstring for the promotion rule. train_baseline_model.py
never calls this; the baseline has no production concept, it only ever
versions for comparison.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_model.py
"""
import logging
import os

import pandas as pd
import xgboost as xgb
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit

import model_common
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

MODEL_NAME = "win-probability"
ALGORITHM = "xgboost"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs. Every other label_* column is excluded
# generically below -- this model only predicts label_home_won, the score
# columns belong to the score-margin model on design/PROJECT_PLAN.md, not
# this one.
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_home_won"
SUMMARY_METRICS = ["accuracy", "log_loss"]
PROMOTION_METRIC = "log_loss"


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return model_common.feature_columns(df, NON_FEATURE_COLUMNS)


_chronological_split = model_common.chronological_split

# A modest, standard XGBoost search space -- deliberately not exhaustive.
# At ~2,700 rows and this few features, a single fit is milliseconds, so
# SEARCH_ITERATIONS * CV_SPLITS fits total finishes in seconds, not
# minutes. max_depth is left at unit steps -- tree depth is already an
# integer, so there's no finer resolution to add.
#
# Floor of 2, not 1 -- v4 and v5 both independently landed on max_depth=1
# (a decision stump: one split per tree, incapable of any feature
# interaction), and each run's model card showed a long, growing list of
# exactly-zero-importance features, including cases like
# home_travel_km=0/away_travel_km=11.4 and away_win_streak=0/
# home_win_streak=12.7 -- two features that should behave symmetrically
# both showing one real and one exactly-zero. That's the fingerprint of a
# stump arbitrarily picking one of two similar features to split on, not
# genuine evidence the zeroed-out one is useless. A floor of 2 still lets
# the search choose shallow trees if they truly generalize best, without
# permitting the single-split degenerate case that confounds every other
# feature's importance reading.
PARAM_DISTRIBUTIONS = {
    "max_depth": [2, 3, 4, 5, 6, 7, 8, 9],
    "n_estimators": [50, 100, 200, 300, 400, 450, 500, 550, 600, 750],
    "learning_rate": [0.001, 0.002, 0.003, 0.005, 0.007, 0.01, 0.02, 0.05, 0.1, 0.2],
    "min_child_weight": [1, 3, 5, 7, 10, 15, 20],
    "subsample": [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0],
}
SEARCH_ITERATIONS = 300
# TimeSeriesSplit divides the training set into CV_SPLITS + 1 chronological
# chunks -- too many splits shrinks the earliest folds' training window
# enough that their score gets noisy.
CV_SPLITS = 8
RANDOM_STATE = 42


def _tune_hyperparameters(X_train: pd.DataFrame, y_train: pd.Series) -> dict:
    """Searches PARAM_DISTRIBUTIONS via RandomizedSearchCV, scored on
    log_loss -- matches the metric already reported/logged, and is the
    right scoring rule for a probability output, not just accuracy.

    Uses TimeSeriesSplit, not plain k-fold. This is deliberate, not
    incidental: k-fold shuffles rows into random folds, which for
    chronological data means a fold's training portion can include games
    that happened AFTER its own validation portion -- the same leakage
    the train/test split itself exists to avoid. TimeSeriesSplit's
    expanding-window folds keep every fold's validation slice strictly
    after its training slice.
    """
    total_fits = SEARCH_ITERATIONS * CV_SPLITS
    logger.info(
        "Starting hyperparameter search: %d candidates x %d CV folds = %d fits",
        SEARCH_ITERATIONS, CV_SPLITS, total_fits,
    )
    # n_jobs=-1 on the search parallelizes across the (candidate, fold)
    # combinations via joblib; n_jobs=1 on the estimator keeps each
    # individual XGBoost fit single-threaded so the two don't oversubscribe
    # the same cores against each other.
    search = RandomizedSearchCV(
        xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", n_jobs=1),
        param_distributions=PARAM_DISTRIBUTIONS,
        n_iter=SEARCH_ITERATIONS,
        scoring="neg_log_loss",
        cv=TimeSeriesSplit(n_splits=CV_SPLITS),
        random_state=RANDOM_STATE,
        # >9 is what unlocks the candidate number in each fit's log line
        # (sklearn's _fit_and_score only appends "; candidate/total" to the
        # "[CV fold/total; candidate/total] END ..." message above that
        # threshold) -- without it every line only shows the fold, not
        # which of the SEARCH_ITERATIONS candidates it belongs to.
        verbose=10,
        n_jobs=-1,
    )
    search.fit(X_train, y_train)
    logger.info(
        "Best hyperparameters: %s (cv log_loss=%.4f)",
        search.best_params_, -search.best_score_,
    )
    return search.best_params_


def train(df: pd.DataFrame) -> tuple[xgb.XGBClassifier, dict]:
    """Trains on the older slice, evaluates on the most recent slice. XGBoost
    handles the NaN values early-season rows carry (no rolling history yet)
    natively -- no imputation needed."""
    feature_columns = _feature_columns(df)
    train_df, test_df = _chronological_split(df, model_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = model_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[LABEL_COLUMN]
    X_test = model_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[LABEL_COLUMN]

    best_params = _tune_hyperparameters(X_train, y_train)
    model = xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **best_params)
    model.fit(X_train, y_train)

    # get_score() only returns features that appear in at least one split
    # -- defaulting the rest to 0.0 makes it possible to see at a glance
    # whether a given feature is contributing at all, not just how much.
    raw_importances = model.get_booster().get_score(importance_type="gain")
    feature_importances = dict(sorted(
        ((col, float(raw_importances.get(col, 0.0))) for col in feature_columns),
        key=lambda kv: kv[1], reverse=True,
    ))

    metrics = model_common.evaluate_holdout(model, X_test, y_test)
    metrics.update({
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        # Dates, not just row counts -- "trained on 2,215 rows" doesn't
        # tell you WHICH games without opening the training dataset
        # itself; the model card should be self-contained.
        "train_date_range": train_date_range,
        "test_date_range": test_date_range,
    })
    logger.info("Holdout accuracy=%.4f log_loss=%.4f", metrics["accuracy"], metrics["log_loss"])

    return model, {
        "feature_columns": feature_columns,
        "feature_importances": feature_importances,
        "hyperparameters": best_params,
        **metrics,
    }


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, EVENT_FEATURES_KEY)
    df = model_common.load_features(s3, EVENT_FEATURES_KEY)
    logger.info("Loaded %d event rows", len(df))

    model, metadata = train(df)

    model_bytes = model.get_booster().save_raw()
    model_card = model_common.save_model_artifact(
        s3, MODEL_NAME, ALGORITHM, model_bytes, "model.xgb", metadata, SUMMARY_METRICS,
    )
    model_common.promote_if_better(s3, MODEL_NAME, model_card["version"], metadata, PROMOTION_METRIC)


if __name__ == "__main__":
    main()
