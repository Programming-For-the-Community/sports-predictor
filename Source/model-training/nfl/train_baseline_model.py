"""
NFL win-probability logistic regression baseline. Reads the same
event_features.parquet as train_model.py, on the identical feature set
and chronological split, and trains a plain L1/L2-regularized logistic
regression instead of XGBoost.

This exists as a comparison point, not a replacement: logistic regression
is the standard sanity-check baseline for sports win-probability models.
If it lands close to train_model.py's accuracy/log_loss, that's evidence
both models are bumping into the same noise ceiling in the data (injuries,
coaching decisions, bounces) rather than XGBoost leaving real predictive
power on the table -- and if XGBoost clears it by a wide margin, that's
evidence of real nonlinear signal a linear model can't capture. See
model_common.py for the plumbing shared with train_model.py.

Versions independently under its own model_name (win-probability-logistic)
via model_common.save_model_artifact, so a baseline run never collides
with or advances the production model's version numbers.

Not scheduled -- run manually via `aws ecs run-task`, overriding the
container command to run this script on the same image as train_model.py
(see Terraform/ecs-task-nfl-train-baseline-model.tf).

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_baseline_model.py
"""
import io
import logging
import os

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import model_common
from library.aws.s3_manager import S3Manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

MODEL_NAME = "win-probability-logistic"
ALGORITHM = "logistic_regression"

# Unlike XGBoost, scikit-learn's LogisticRegression can't handle NaN or
# wildly different feature scales natively -- rolling-average columns
# range from percentages (0-1) to yards (0-500) to travel km (0-10000),
# and L1/L2 regularization penalizes coefficient magnitude, so an
# unscaled feature gets penalized unevenly just for having a bigger raw
# scale. Median imputation and standardization are both steps in the
# model pipeline itself, fit only on the training slice and applied to
# the test slice -- same leakage rule as the chronological split -- so
# model_common.evaluate_holdout can call predict()/predict_proba()
# directly on raw numeric_frame() output, exactly like it does for
# XGBoost.
#
# A small, exhaustive grid (22 combinations) rather than a randomized
# search -- unlike train_model.py's much larger XGBoost space, this is
# cheap enough to search in full. liblinear supports both penalties
# without the extra l1_ratio parameter elasticnet would need.
PARAM_GRID = {
    "model__C": [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100],
    "model__penalty": ["l1", "l2"],
}
CV_SPLITS = 8
RANDOM_STATE = 42


def _build_pipeline() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(solver="liblinear", max_iter=1000, random_state=RANDOM_STATE)),
    ])


def _tune_hyperparameters(X_train: pd.DataFrame, y_train: pd.Series) -> dict:
    """Searches PARAM_GRID via GridSearchCV, scored on log_loss -- same
    scoring rule and TimeSeriesSplit rationale as train_model.py's
    _tune_hyperparameters (a plain k-fold would leak future games into a
    fold's training portion)."""
    total_fits = len(PARAM_GRID["model__C"]) * len(PARAM_GRID["model__penalty"]) * CV_SPLITS
    logger.info("Starting hyperparameter search: %d fits", total_fits)
    search = GridSearchCV(
        _build_pipeline(),
        param_grid=PARAM_GRID,
        scoring="neg_log_loss",
        cv=TimeSeriesSplit(n_splits=CV_SPLITS),
        # >9 unlocks the candidate number in each fit's log line -- same
        # reasoning as train_model.py's verbose=10.
        verbose=10,
        n_jobs=-1,
    )
    search.fit(X_train, y_train)
    logger.info(
        "Best hyperparameters: %s (cv log_loss=%.4f)",
        search.best_params_, -search.best_score_,
    )
    return search.best_params_


def train(df: pd.DataFrame) -> tuple[Pipeline, dict]:
    feature_columns = model_common.feature_columns(df)
    train_df, test_df = model_common.chronological_split(df, model_common.TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    X_train = model_common.numeric_frame(train_df, feature_columns)
    y_train = train_df[model_common.LABEL_COLUMN]
    X_test = model_common.numeric_frame(test_df, feature_columns)
    y_test = test_df[model_common.LABEL_COLUMN]

    best_params = _tune_hyperparameters(X_train, y_train)
    model = _build_pipeline().set_params(**best_params)
    model.fit(X_train, y_train)

    # Standardized coefficients, not XGBoost-style gain -- signed (positive
    # raises home win probability, negative lowers it) and comparable
    # across features precisely because every input was standardized to
    # the same scale first. An L1 penalty can zero a coefficient out
    # entirely, which is its own form of feature selection worth comparing
    # against XGBoost's zero-gain features on the same model card.
    coefficients = model.named_steps["model"].coef_[0]
    feature_coefficients = dict(sorted(
        zip(feature_columns, (float(c) for c in coefficients)),
        key=lambda kv: abs(kv[1]), reverse=True,
    ))

    metrics = model_common.evaluate_holdout(model, X_test, y_test)
    metrics.update({
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_date_range": train_date_range,
        "test_date_range": test_date_range,
    })
    logger.info("Holdout accuracy=%.4f log_loss=%.4f", metrics["accuracy"], metrics["log_loss"])

    return model, {
        "feature_columns": feature_columns,
        "feature_coefficients": feature_coefficients,
        # Strips the pipeline step prefix ("model__C" -> "C") -- that
        # prefix is an implementation detail of _build_pipeline's step
        # name, not something worth exposing on the model card.
        "hyperparameters": {key.removeprefix("model__"): value for key, value in best_params.items()},
        **metrics,
    }


def main() -> None:
    bucket = os.environ["MODEL_ARTIFACTS_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION")
    s3 = S3Manager(bucket, region=region)

    logger.info("Loading %s training data from s3://%s/%s", MODEL_NAME, bucket, model_common.EVENT_FEATURES_KEY)
    df = model_common.load_features(s3)
    logger.info("Loaded %d event rows", len(df))

    model, metadata = train(df)

    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    model_common.save_model_artifact(s3, MODEL_NAME, ALGORITHM, buffer.getvalue(), "model.joblib", metadata)


if __name__ == "__main__":
    main()
