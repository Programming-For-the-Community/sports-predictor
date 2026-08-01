"""
NFL win-probability model training. Reads event_features.parquet (written
by Source/feature-engineering/nfl/build_dataset.py) from S3, searches
XGBoost hyperparameters (see _tune_hyperparameters), trains the final
binary classifier predicting label_home_won, and writes a versioned model
artifact plus a model card (training window, row counts, hyperparameters,
and holdout metrics -- everything needed to know what a given version
actually is without re-running or cross-referencing logs) to S3.

Not scheduled -- run manually via `aws ecs run-task`, same as
Source/feature-engineering/nfl (see Terraform/ecs-task-nfl-train-model.tf).
Each run trains a brand-new version under its own prefix; it never
overwrites a previous one -- see library.storage.model_artifacts for the
versioning scheme, and Terraform/s3-model-artifacts.tf for why versioning
is per-model rather than one shared counter across every model this sport
has.

Required environment variables:
    MODEL_ARTIFACTS_BUCKET_NAME
    AWS_REGION

Usage:
    python train_model.py
"""
import io
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit

from library.aws.s3_manager import S3Manager
from library.storage.model_artifacts import model_artifact_key, model_artifact_prefix, next_model_version

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nfl-train-model")

SPORT = "nfl"
MODEL_NAME = "win-probability"
EVENT_FEATURES_KEY = "nfl/training-data/event_features.parquet"

# Identifiers, never model inputs. Every other label_* column is excluded
# generically below (this model only predicts label_home_won -- the score
# columns belong to a future score-margin model, not this one).
NON_FEATURE_COLUMNS = {"event_key", "event_date", "home_entity_id", "away_entity_id", "venue_city", "venue_state"}
LABEL_COLUMN = "label_home_won"

# Fraction of events (most recent, by date) held out for evaluation.
# Chronological, not random -- a random split would leak future games
# into training in a way that doesn't match how this model actually gets
# used: predicting games that haven't happened yet from ones that have.
TEST_FRACTION = 0.2

# A modest, standard XGBoost search space -- deliberately not exhaustive.
# At ~2,700 rows and this few features, a single fit is milliseconds, so
# SEARCH_ITERATIONS * CV_SPLITS fits total finishes in seconds, not
# minutes. max_depth is left at unit steps -- tree depth is already an
# integer, so there's no finer resolution to add.
PARAM_DISTRIBUTIONS = {
    "max_depth": [1, 2, 3, 4, 5, 6],
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


def _load_features(s3: S3Manager) -> pd.DataFrame:
    data = s3.get_bytes(EVENT_FEATURES_KEY)
    return pd.read_parquet(io.BytesIO(data))


def _feature_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in df.columns if col not in NON_FEATURE_COLUMNS and not col.startswith("label_")]


def _chronological_split(df: pd.DataFrame, test_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("event_date")
    split_index = int(len(ordered) * (1 - test_fraction))
    return ordered.iloc[:split_index], ordered.iloc[split_index:]


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
        verbose=2,
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
    train_df, test_df = _chronological_split(df, TEST_FRACTION)
    train_date_range = [str(train_df["event_date"].min()), str(train_df["event_date"].max())]
    test_date_range = [str(test_df["event_date"].min()), str(test_df["event_date"].max())]
    logger.info(
        "Training on %d rows (%s to %s), evaluating on %d rows (%s to %s)",
        len(train_df), *train_date_range, len(test_df), *test_date_range,
    )

    # A feature column that's entirely null in this training window (e.g.
    # weather_temperature, which real ESPN history frequently doesn't
    # report) has no non-null value for pandas to infer a numeric dtype
    # from, so it comes back from Parquet as dtype `object` rather than
    # float64 -- and XGBoost rejects `object` columns outright, even when
    # every value is just a missing float. Coercing explicitly (bools
    # included) guarantees every feature column is numeric before it ever
    # reaches XGBoost, regardless of how sparse any single column is.
    X_train = train_df[feature_columns].apply(pd.to_numeric, errors="coerce")
    y_train = train_df[LABEL_COLUMN]
    X_test = test_df[feature_columns].apply(pd.to_numeric, errors="coerce")
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

    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)[:, 1]
    metrics = {
        # float()/int() casts -- sklearn returns numpy scalar types, which
        # json.dumps() (via S3Manager.put_json) doesn't know how to
        # serialize, the same lesson learned from DynamoDB's Decimal.
        "accuracy": float(accuracy_score(y_test, predictions)),
        "log_loss": float(log_loss(y_test, probabilities)),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        # Dates, not just row counts -- "trained on 2,215 rows" doesn't
        # tell you WHICH games without opening the training dataset
        # itself; the model card should be self-contained.
        "train_date_range": train_date_range,
        "test_date_range": test_date_range,
    }
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
    df = _load_features(s3)
    logger.info("Loaded %d event rows", len(df))

    model, metadata = train(df)

    prefix = model_artifact_prefix(SPORT, MODEL_NAME)
    version = next_model_version(s3.list_keys(prefix))
    model_key = model_artifact_key(SPORT, MODEL_NAME, version, "model.xgb")
    model_card_key = model_artifact_key(SPORT, MODEL_NAME, version, "model_card.json")

    model_bytes = model.get_booster().save_raw()
    s3.put_bytes(model_key, model_bytes, content_type="application/octet-stream")
    logger.info("Wrote model artifact to s3://%s/%s", bucket, model_key)

    # Everything needed to answer "what is this version, and how well did
    # it do" without re-running anything or cross-referencing CloudWatch
    # Logs -- training window, row counts, hyperparameters, and holdout
    # metrics, all in one file stored right next to the model it describes.
    model_card = {
        "sport": SPORT,
        "model_name": MODEL_NAME,
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    s3.put_json(model_card_key, model_card)
    logger.info("Wrote model card to s3://%s/%s", bucket, model_card_key)

    # One line with the version and its score together -- there's no
    # backtesting harness yet (design/PROJECT_PLAN.md Phase 4), so this is
    # the fastest way to eyeball "did this week's retrain look reasonable"
    # straight from CloudWatch Logs without opening metadata.json.
    logger.info(
        "Training complete: %s v%d -- accuracy=%.4f log_loss=%.4f (%d train rows, %d test rows)",
        MODEL_NAME, version, metadata["accuracy"], metadata["log_loss"],
        metadata["train_rows"], metadata["test_rows"],
    )


if __name__ == "__main__":
    main()
