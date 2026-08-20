"""
One adapter per trainable algorithm, used by both training
(library.ml.backtest.run_backtest) and serving
(Source/aws-lambdas/nfl/predict/model_loader.py) so neither side hardcodes
which algorithm a given model_name's promoted version happens to be.

LightGBMClassifierAdapter/LightGBMRegressorAdapter import the `lightgbm`
package lazily, inside their own methods, so importing this module never
requires lightgbm to be installed.

predict() always returns positive-class probability for a
classification-task target, or the predicted continuous value for a
regression-task target. Every method operates on a pandas DataFrame,
whether that's a many-row training/holdout frame or a single-row live
prediction.

XGBoostAdapter's `estimator` is always a raw xgb.Booster, never the
sklearn XGBClassifier/XGBRegressor wrapper -- tune_and_fit uses the
sklearn wrapper internally (RandomizedSearchCV needs it) but converts to a
Booster via get_booster() before returning, so predict/serialize/
deserialize/feature_importances only ever handle one representation. A
single XGBoostAdapter() instance (task=None) covers serving's algorithm
registry for both classifier and regressor model cards.
"""
import io
import logging
from typing import Any, Protocol

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LogisticRegression
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV, TimeSeriesSplit

logger = logging.getLogger("model-training")


def _log_search_convergence(label: str, search: RandomizedSearchCV) -> None:
    """Logs the best score RandomizedSearchCV had found so far at several
    checkpoints through its trial order, from search.cv_results_.
    Assumes a "neg_*" scoring metric (neg_log_loss/
    neg_root_mean_squared_error), so higher (less negative) is always
    better.

    Best-effort, never raises -- this is a diagnostic-only side effect of
    an already-successful search.fit()."""
    try:
        scores = np.maximum.accumulate(search.cv_results_["mean_test_score"])
        n = len(scores)
        checkpoints = sorted({max(1, round(n * frac)) for frac in (0.1, 0.25, 0.5, 0.75, 1.0)})
        summary = ", ".join(f"{c}/{n}={scores[c - 1]:.5f}" for c in checkpoints)
        logger.info("%s search convergence (best score so far, at iteration/total): %s", label, summary)
    except Exception:
        logger.debug("Couldn't summarize search convergence for %s", label, exc_info=True)
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class ModelAdapter(Protocol):
    algorithm: str
    artifact_filename: str

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Any, dict]:
        """Searches this algorithm's own hyperparameter space and returns
        (fitted estimator, best hyperparameters) -- the search strategy
        (grid vs. randomized, CV splitter, scoring rule) is entirely this
        algorithm's own concern. Training-only; serving never calls this."""

    def predict(self, estimator: Any, X: pd.DataFrame) -> np.ndarray: ...

    def feature_importances(self, estimator: Any, feature_columns: list[str]) -> dict[str, float]: ...

    def serialize(self, estimator: Any) -> bytes: ...

    def deserialize(self, raw: bytes) -> Any: ...


class XGBoostAdapter:
    """Concrete on its own for serving (predict/serialize/deserialize/
    feature_importances need no task information) -- tune_and_fit raises
    if called without a task; training goes through the classification/
    regression subclasses below, which set self.task before delegating
    here."""

    algorithm = "xgboost"
    artifact_filename = "model.xgb"

    def __init__(self, task: str | None = None):
        self.task = task

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[xgb.Booster, dict]:
        raise NotImplementedError("Use XGBoostClassifierAdapter or XGBoostRegressorAdapter to train.")

    def predict(self, estimator: xgb.Booster, X: pd.DataFrame) -> np.ndarray:
        # feature_names must match the booster's training column order.
        matrix = xgb.DMatrix(X.to_numpy(dtype=float), feature_names=list(X.columns))
        return estimator.predict(matrix)

    def feature_importances(self, estimator: xgb.Booster, feature_columns: list[str]) -> dict[str, float]:
        # get_score() only returns features that appear in at least one
        # split; missing ones default to 0.0.
        raw_importances = estimator.get_score(importance_type="gain")
        return dict(sorted(
            ((col, float(raw_importances.get(col, 0.0))) for col in feature_columns),
            key=lambda kv: kv[1], reverse=True,
        ))

    def serialize(self, estimator: xgb.Booster) -> bytes:
        return estimator.save_raw()

    def deserialize(self, raw: bytes) -> xgb.Booster:
        booster = xgb.Booster()
        booster.load_model(bytearray(raw))
        return booster


# Shared search space across classifier and regressor. max_depth floors
# at 2, not 1: a depth-1 stump can't model any feature interaction.
_XGB_PARAM_DISTRIBUTIONS = {
    "max_depth": [2, 3, 4, 5, 6, 7, 8, 9],
    "n_estimators": [50, 100, 200, 300, 400, 450, 500, 550, 600, 750],
    "learning_rate": [0.001, 0.002, 0.003, 0.005, 0.007, 0.01, 0.02, 0.05, 0.1, 0.2],
    "min_child_weight": [1, 3, 5, 7, 10, 15, 20],
    "subsample": [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0],
    "colsample_bytree": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
}
_XGB_SEARCH_ITERATIONS = 250
_XGB_CV_SPLITS = 8
_XGB_RANDOM_STATE = 42

class XGBoostClassifierAdapter(XGBoostAdapter):
    def __init__(self):
        super().__init__(task="classification")

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[xgb.Booster, dict]:
        search = RandomizedSearchCV(
            xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", n_jobs=1),
            param_distributions=_XGB_PARAM_DISTRIBUTIONS,
            n_iter=_XGB_SEARCH_ITERATIONS,
            scoring="neg_log_loss",
            cv=TimeSeriesSplit(n_splits=_XGB_CV_SPLITS),
            random_state=_XGB_RANDOM_STATE,
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        _log_search_convergence("xgboost_classifier", search)
        model = xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **search.best_params_)
        model.fit(X_train, y_train)
        return model.get_booster(), search.best_params_


class XGBoostRegressorAdapter(XGBoostAdapter):
    def __init__(self):
        super().__init__(task="regression")

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[xgb.Booster, dict]:
        search = RandomizedSearchCV(
            xgb.XGBRegressor(objective="reg:squarederror", n_jobs=1),
            param_distributions=_XGB_PARAM_DISTRIBUTIONS,
            n_iter=_XGB_SEARCH_ITERATIONS,
            scoring="neg_root_mean_squared_error",
            cv=TimeSeriesSplit(n_splits=_XGB_CV_SPLITS),
            random_state=_XGB_RANDOM_STATE,
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        _log_search_convergence("xgboost_regressor", search)
        model = xgb.XGBRegressor(objective="reg:squarederror", **search.best_params_)
        model.fit(X_train, y_train)
        return model.get_booster(), search.best_params_


class _JoblibSerializedAdapter:
    """Shared serialize/deserialize for every adapter whose estimator is a
    plain scikit-learn object (a Pipeline, in every case below) --
    picklable via joblib, unlike XGBoostAdapter's raw Booster bytes.
    Doesn't cover tune_and_fit/predict/feature_importances, which differ
    per algorithm and (for a classifier/regressor pair of the same
    algorithm) per task -- predict_proba only exists on a classifier."""

    def serialize(self, estimator) -> bytes:
        buffer = io.BytesIO()
        joblib.dump(estimator, buffer)
        return buffer.getvalue()

    def deserialize(self, raw: bytes):
        return joblib.load(io.BytesIO(raw))


# Exhaustive, not randomized -- 32 combinations is cheap enough to search
# in full, unlike XGBoost's much larger space above. liblinear supports
# both penalties without the extra l1_ratio parameter elasticnet would
# need. Denser around 0.01-1 than the decade-spaced tail ends -- that's
# typically where a log-loss-vs-C curve bends, so it's worth resolving
# more finely than the extremes, which mostly just confirm the curve has
# flattened out.
_LOGISTIC_PARAM_GRID = {
    "model__C": [0.001, 0.003, 0.01, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 3, 5, 10, 30, 100],
    "model__penalty": ["l1", "l2"],
}
_LOGISTIC_CV_SPLITS = 8
_LOGISTIC_RANDOM_STATE = 42


class LogisticRegressionAdapter(_JoblibSerializedAdapter):
    """A classification-only candidate for win-probability-type targets.
    scikit-learn's LogisticRegression can't handle NaN or differently
    scaled features natively, and L1/L2 regularization penalizes
    coefficient magnitude, so an unscaled feature gets penalized unevenly
    just for having a bigger raw scale. Median imputation and
    standardization are both steps in the pipeline, fit only on the
    training slice, so the fitted Pipeline can run directly on raw
    (NaN-containing) X at predict time."""

    algorithm = "logistic_regression"
    artifact_filename = "model.joblib"

    def _build_pipeline(self) -> Pipeline:
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(solver="liblinear", max_iter=1000, random_state=_LOGISTIC_RANDOM_STATE)),
        ])

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Pipeline, dict]:
        search = GridSearchCV(
            self._build_pipeline(),
            param_grid=_LOGISTIC_PARAM_GRID,
            scoring="neg_log_loss",
            cv=TimeSeriesSplit(n_splits=_LOGISTIC_CV_SPLITS),
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        # Strips the pipeline step prefix ("model__C" -> "C").
        best_params = {key.removeprefix("model__"): value for key, value in search.best_params_.items()}
        model = self._build_pipeline().set_params(**{f"model__{k}": v for k, v in best_params.items()})
        model.fit(X_train, y_train)
        return model, best_params

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict_proba(X)[:, 1]

    def feature_importances(self, estimator: Pipeline, feature_columns: list[str]) -> dict[str, float]:
        # Standardized coefficients: signed (positive raises the predicted
        # probability, negative lowers it) and comparable across features
        # since every input was standardized to the same scale.
        coefficients = estimator.named_steps["model"].coef_[0]
        return dict(sorted(
            zip(feature_columns, (float(c) for c in coefficients)),
            key=lambda kv: abs(kv[1]), reverse=True,
        ))


_ELASTIC_NET_PARAM_GRID = {
    "model__alpha": [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0],
    "model__l1_ratio": [0.1, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99],
}
_ELASTIC_NET_CV_SPLITS = 8
_ELASTIC_NET_RANDOM_STATE = 42


class ElasticNetAdapter(_JoblibSerializedAdapter):
    algorithm = "elastic_net"
    artifact_filename = "model.joblib"

    def _build_pipeline(self) -> Pipeline:
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", ElasticNet(max_iter=5000, random_state=_ELASTIC_NET_RANDOM_STATE)),
        ])

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Pipeline, dict]:
        search = GridSearchCV(
            self._build_pipeline(),
            param_grid=_ELASTIC_NET_PARAM_GRID,
            scoring="neg_root_mean_squared_error",
            cv=TimeSeriesSplit(n_splits=_ELASTIC_NET_CV_SPLITS),
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        best_params = {key.removeprefix("model__"): value for key, value in search.best_params_.items()}
        model = self._build_pipeline().set_params(**{f"model__{k}": v for k, v in best_params.items()})
        model.fit(X_train, y_train)
        return model, best_params

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict(X)

    def feature_importances(self, estimator: Pipeline, feature_columns: list[str]) -> dict[str, float]:
        coefficients = estimator.named_steps["model"].coef_
        return dict(sorted(
            zip(feature_columns, (float(c) for c in coefficients)),
            key=lambda kv: abs(kv[1]), reverse=True,
        ))


# Bagged trees: each tree is fit independently on a bootstrap resample,
# then averaged/voted, rather than each tree correcting the last one's
# residuals. max_samples caps how much of the training slice each tree's
# bootstrap draw sees, bounding per-tree fit cost. max_features has no
# None option: an uncapped draw (every split considering every feature)
# is pure cost with no variance-reduction benefit.
_RF_PARAM_DISTRIBUTIONS = {
    "model__n_estimators": [100, 200, 300, 400, 500, 600],
    "model__max_depth": [4, 6, 8, 10, 15, 20, None],
    "model__min_samples_leaf": [1, 2, 4, 8],
    "model__max_features": ["sqrt", "log2"],
    "model__max_samples": [0.4, 0.5, 0.6, 0.7, 0.85],
}
_RF_SEARCH_ITERATIONS = 70
_RF_CV_SPLITS = 8
_RF_RANDOM_STATE = 42


class _RandomForestAdapterBase(_JoblibSerializedAdapter):
    artifact_filename = "model.joblib"
    _estimator_cls: type = None
    _scoring: str = None

    def _build_pipeline(self) -> Pipeline:
        # No StandardScaler -- trees split on raw thresholds. SimpleImputer
        # is still needed since scikit-learn's tree ensembles don't handle
        # NaN natively.
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("model", self._estimator_cls(random_state=_RF_RANDOM_STATE, n_jobs=1)),
        ])

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Pipeline, dict]:
        search = RandomizedSearchCV(
            self._build_pipeline(),
            param_distributions=_RF_PARAM_DISTRIBUTIONS,
            n_iter=_RF_SEARCH_ITERATIONS,
            scoring=self._scoring,
            cv=TimeSeriesSplit(n_splits=_RF_CV_SPLITS),
            random_state=_RF_RANDOM_STATE,
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        _log_search_convergence(self.algorithm, search)
        best_params = {key.removeprefix("model__"): value for key, value in search.best_params_.items()}
        model = self._build_pipeline().set_params(**{f"model__{k}": v for k, v in best_params.items()})
        model.fit(X_train, y_train)
        return model, best_params

    def feature_importances(self, estimator: Pipeline, feature_columns: list[str]) -> dict[str, float]:
        importances = estimator.named_steps["model"].feature_importances_
        return dict(sorted(
            zip(feature_columns, (float(v) for v in importances)),
            key=lambda kv: kv[1], reverse=True,
        ))


class RandomForestClassifierAdapter(_RandomForestAdapterBase):
    algorithm = "random_forest_classifier"
    _estimator_cls = RandomForestClassifier
    _scoring = "neg_log_loss"

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict_proba(X)[:, 1]


class RandomForestRegressorAdapter(_RandomForestAdapterBase):
    algorithm = "random_forest_regressor"
    _estimator_cls = RandomForestRegressor
    _scoring = "neg_root_mean_squared_error"

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict(X)


# A small neural net, kept small (max 64 units) and regularized
# (early_stopping, an alpha search) since the current dataset size would
# let a bigger network overfit.
_MLP_PARAM_DISTRIBUTIONS = {
    "model__hidden_layer_sizes": [(32,), (64,), (32, 16), (64, 32)],
    "model__alpha": [0.0001, 0.001, 0.01, 0.1],
    "model__learning_rate_init": [0.001, 0.003, 0.01],
}
_MLP_SEARCH_ITERATIONS = 40
_MLP_CV_SPLITS = 8
_MLP_RANDOM_STATE = 42


class _MLPAdapterBase(_JoblibSerializedAdapter):
    artifact_filename = "model.joblib"
    _estimator_cls: type = None
    _scoring: str = None

    def _build_pipeline(self) -> Pipeline:
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", self._estimator_cls(
                max_iter=2000, early_stopping=True, random_state=_MLP_RANDOM_STATE,
            )),
        ])

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Pipeline, dict]:
        search = RandomizedSearchCV(
            self._build_pipeline(),
            param_distributions=_MLP_PARAM_DISTRIBUTIONS,
            n_iter=_MLP_SEARCH_ITERATIONS,
            scoring=self._scoring,
            cv=TimeSeriesSplit(n_splits=_MLP_CV_SPLITS),
            random_state=_MLP_RANDOM_STATE,
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        _log_search_convergence(self.algorithm, search)
        best_params = {key.removeprefix("model__"): value for key, value in search.best_params_.items()}
        model = self._build_pipeline().set_params(**{f"model__{k}": v for k, v in best_params.items()})
        model.fit(X_train, y_train)
        return model, best_params

    def feature_importances(self, estimator: Pipeline, feature_columns: list[str]) -> dict[str, float]:
        # MLPs have no native feature-importance concept the way trees or
        # linear coefficients do; an empty dict is honest about that.
        return {}


class MLPClassifierAdapter(_MLPAdapterBase):
    algorithm = "mlp_classifier"
    _estimator_cls = MLPClassifier
    _scoring = "neg_log_loss"

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict_proba(X)[:, 1]


class MLPRegressorAdapter(_MLPAdapterBase):
    algorithm = "mlp_regressor"
    _estimator_cls = MLPRegressor
    _scoring = "neg_root_mean_squared_error"

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict(X)


# Histogram-based gradient boosting: native missing-value handling, no
# imputer needed, joblib-serializable sklearn wrapper unlike XGBoost's raw
# Booster. `lightgbm` is imported lazily here so this module doesn't
# require it installed.
def _lgbm_estimator_classes():
    import lightgbm as lgb
    return lgb.LGBMClassifier, lgb.LGBMRegressor


_LGBM_PARAM_DISTRIBUTIONS = {
    "max_depth": [-1, 3, 4, 5, 6, 7, 8, 9],
    "num_leaves": [15, 31, 63, 127],
    "n_estimators": [50, 100, 200, 300, 400, 500],
    "learning_rate": [0.005, 0.01, 0.02, 0.05, 0.1, 0.2],
    "min_child_samples": [5, 10, 20, 30, 50],
    "subsample": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "colsample_bytree": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
}
_LGBM_SEARCH_ITERATIONS = 200
_LGBM_CV_SPLITS = 8
_LGBM_RANDOM_STATE = 42


class LightGBMClassifierAdapter(_JoblibSerializedAdapter):
    algorithm = "lightgbm_classifier"
    artifact_filename = "model.joblib"

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Any, dict]:
        LGBMClassifier, _ = _lgbm_estimator_classes()
        search = RandomizedSearchCV(
            LGBMClassifier(objective="binary", n_jobs=1, verbosity=-1, random_state=_LGBM_RANDOM_STATE),
            param_distributions=_LGBM_PARAM_DISTRIBUTIONS,
            n_iter=_LGBM_SEARCH_ITERATIONS,
            scoring="neg_log_loss",
            cv=TimeSeriesSplit(n_splits=_LGBM_CV_SPLITS),
            random_state=_LGBM_RANDOM_STATE,
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        _log_search_convergence("lightgbm_classifier", search)
        model = LGBMClassifier(objective="binary", verbosity=-1, random_state=_LGBM_RANDOM_STATE, **search.best_params_)
        model.fit(X_train, y_train)
        return model, search.best_params_

    def predict(self, estimator, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict_proba(X)[:, 1]

    def feature_importances(self, estimator, feature_columns: list[str]) -> dict[str, float]:
        importances = estimator.feature_importances_
        return dict(sorted(
            zip(feature_columns, (float(v) for v in importances)),
            key=lambda kv: kv[1], reverse=True,
        ))


class LightGBMRegressorAdapter(_JoblibSerializedAdapter):
    algorithm = "lightgbm_regressor"
    artifact_filename = "model.joblib"

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[Any, dict]:
        _, LGBMRegressor = _lgbm_estimator_classes()
        search = RandomizedSearchCV(
            LGBMRegressor(objective="regression", n_jobs=1, verbosity=-1, random_state=_LGBM_RANDOM_STATE),
            param_distributions=_LGBM_PARAM_DISTRIBUTIONS,
            n_iter=_LGBM_SEARCH_ITERATIONS,
            scoring="neg_root_mean_squared_error",
            cv=TimeSeriesSplit(n_splits=_LGBM_CV_SPLITS),
            random_state=_LGBM_RANDOM_STATE,
            verbose=10,
            n_jobs=-1,
        )
        search.fit(X_train, y_train)
        _log_search_convergence("lightgbm_regressor", search)
        model = LGBMRegressor(objective="regression", verbosity=-1, random_state=_LGBM_RANDOM_STATE, **search.best_params_)
        model.fit(X_train, y_train)
        return model, search.best_params_

    def predict(self, estimator, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict(X)

    def feature_importances(self, estimator, feature_columns: list[str]) -> dict[str, float]:
        importances = estimator.feature_importances_
        return dict(sorted(
            zip(feature_columns, (float(v) for v in importances)),
            key=lambda kv: kv[1], reverse=True,
        ))


# Keyed by model_card["algorithm"]. Serving dispatches a promoted model
# card's "algorithm" field through this registry to deserialize/predict
# it. Only covers predict/serialize/deserialize/feature_importances;
# training scripts import the classifier/regressor-specific classes
# directly to build their own candidate lists. XGBoostAdapter's
# predict/serialize/deserialize are identical regardless of task, so one
# shared instance covers both here; every other algorithm needs a
# distinct registry entry per task.
ADAPTERS: dict[str, ModelAdapter] = {
    "xgboost": XGBoostAdapter(),
    "logistic_regression": LogisticRegressionAdapter(),
    "elastic_net": ElasticNetAdapter(),
    "random_forest_classifier": RandomForestClassifierAdapter(),
    "random_forest_regressor": RandomForestRegressorAdapter(),
    "mlp_classifier": MLPClassifierAdapter(),
    "mlp_regressor": MLPRegressorAdapter(),
    "lightgbm_classifier": LightGBMClassifierAdapter(),
    "lightgbm_regressor": LightGBMRegressorAdapter(),
}
