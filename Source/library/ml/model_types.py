"""
One adapter per trainable algorithm, used by both training
(library.ml.backtest.run_backtest) and serving
(Source/aws-lambdas/nfl/predict/model_loader.py) so neither side hardcodes
which algorithm a given model_name's promoted version happens to be.
Adding a genuinely new algorithm (this module already has five: XGBoost,
logistic regression, ElasticNet, Random Forest, and a small MLP neural
net) means writing one adapter class and adding it to a target's
candidate list -- no new training script, no new ECS task, no new
scheduler entry, and nothing to change in model_loader.py.

predict() always means the same thing regardless of algorithm or which
side of the train/serve boundary is calling it: positive-class probability
for a classification-task target, the predicted continuous value for a
regression-task target. Every method operates on a pandas DataFrame -- both
training-time holdout evaluation (many rows) and serving-time live
prediction (one row, wrapped in a 1-row DataFrame by model_loader.py) go
through the exact same code path. Accuracy (which needs a thresholded
label, not a probability) is derived from predict()'s output externally,
in library.ml.training_common.evaluate_holdout -- no adapter needs its own
notion of a decision threshold.

XGBoostAdapter's `estimator` is always a raw xgb.Booster, never the
sklearn XGBClassifier/XGBRegressor wrapper -- tune_and_fit uses the
sklearn wrapper internally (RandomizedSearchCV needs it), but converts to
a Booster via get_booster() before returning, so predict/serialize/
deserialize/feature_importances only ever have to handle one
representation, identical regardless of whether the target underneath is
a classifier or regressor -- which is why a single XGBoostAdapter()
instance (task=None) is enough for serving's algorithm registry, even
though training needs the classification/regression-specific
XGBoostClassifierAdapter/XGBoostRegressorAdapter subclasses to know which
objective/scoring to tune with.
"""
import io
import os
from typing import Any, Protocol

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LogisticRegression
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV, TimeSeriesSplit
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
    if called without a task, since serving never calls it and training
    always goes through the classification/regression subclasses below,
    which set self.task before delegating here."""

    algorithm = "xgboost"
    artifact_filename = "model.xgb"

    def __init__(self, task: str | None = None):
        self.task = task

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[xgb.Booster, dict]:
        raise NotImplementedError("Use XGBoostClassifierAdapter or XGBoostRegressorAdapter to train.")

    def predict(self, estimator: xgb.Booster, X: pd.DataFrame) -> np.ndarray:
        # feature_names must match what the booster was trained on, in the
        # same order -- X's own column order, which every caller (training's
        # numeric_frame, serving's single-row frame) already builds from
        # the model card's own feature_columns list.
        matrix = xgb.DMatrix(X.to_numpy(dtype=float), feature_names=list(X.columns))
        return estimator.predict(matrix)

    def feature_importances(self, estimator: xgb.Booster, feature_columns: list[str]) -> dict[str, float]:
        # get_score() only returns features that appear in at least one
        # split -- defaulting the rest to 0.0 makes it possible to see at
        # a glance whether a given feature is contributing at all, not
        # just how much.
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


# Shared search shape across classifier and regressor, used by
# train_win_probability_model.py/train_score_model.py/train_player_prop_model.py.
# max_depth floors at 2, not 1: a depth-1 decision stump can't model any
# feature interaction -- it shows up as paired features like
# home_travel_km/away_travel_km with one real and one exactly-zero
# importance, the fingerprint of a stump arbitrarily picking one of two
# similar features to split on. colsample_bytree is subsample's
# per-tree-features counterpart (row subsampling alone leaves every tree
# free to lean on the same dominant feature) -- searched at the same
# granularity as subsample so neither axis is favored over the other.
_XGB_PARAM_DISTRIBUTIONS = {
    "max_depth": [2, 3, 4, 5, 6, 7, 8, 9],
    "n_estimators": [50, 100, 200, 300, 400, 450, 500, 550, 600, 750],
    "learning_rate": [0.001, 0.002, 0.003, 0.005, 0.007, 0.01, 0.02, 0.05, 0.1, 0.2],
    "min_child_weight": [1, 3, 5, 7, 10, 15, 20],
    "subsample": [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0],
    "colsample_bytree": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
}
# Raised alongside colsample_bytree -- adding a 6th search axis grows the
# combination space roughly 7x, and holding n_iter fixed would have let
# RandomizedSearchCV's sample density (iterations / combinations) drop by
# the same factor.
_XGB_SEARCH_ITERATIONS = 400
_XGB_CV_SPLITS = 8
_XGB_RANDOM_STATE = 42

# Unset (the default) leaves every XGBoost fit exactly as it's always
# been -- CPU, tree_method left at XGBoost's own default. Set to e.g.
# "cuda" to GPU-accelerate the actual tree-building on a GPU-enabled
# instance (SageMaker script mode, not Fargate -- see
# Source/model-training/ncaafb/sagemaker_gpu_poc/ for the one-off
# benchmark this exists for).
_XGB_GPU_DEVICE = os.environ.get("XGBOOST_DEVICE")


def _xgb_estimator_kwargs() -> dict:
    """Extra kwargs merged into every XGBRegressor/XGBClassifier
    construction below -- empty (today's unchanged behavior) unless
    _XGB_GPU_DEVICE is set."""
    if not _XGB_GPU_DEVICE:
        return {}
    return {"tree_method": "hist", "device": _XGB_GPU_DEVICE}


def _xgb_search_n_jobs() -> int:
    """RandomizedSearchCV's own n_jobs -- -1 (today's default) fans the
    search's _XGB_SEARCH_ITERATIONS * _XGB_CV_SPLITS = 3,200 independent
    fits out across every CPU core. That's exactly wrong for a single
    GPU: parallel workers would contend for the one device instead of
    speeding anything up, so this drops to 1 (fits run one at a time,
    each one itself GPU-accelerated) whenever _XGB_GPU_DEVICE is set."""
    return 1 if _XGB_GPU_DEVICE else -1


class XGBoostClassifierAdapter(XGBoostAdapter):
    def __init__(self):
        super().__init__(task="classification")

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[xgb.Booster, dict]:
        search = RandomizedSearchCV(
            xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", n_jobs=1, **_xgb_estimator_kwargs()),
            param_distributions=_XGB_PARAM_DISTRIBUTIONS,
            n_iter=_XGB_SEARCH_ITERATIONS,
            scoring="neg_log_loss",
            cv=TimeSeriesSplit(n_splits=_XGB_CV_SPLITS),
            random_state=_XGB_RANDOM_STATE,
            verbose=10,
            n_jobs=_xgb_search_n_jobs(),
        )
        search.fit(X_train, y_train)
        model = xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **search.best_params_, **_xgb_estimator_kwargs())
        model.fit(X_train, y_train)
        return model.get_booster(), search.best_params_


class XGBoostRegressorAdapter(XGBoostAdapter):
    def __init__(self):
        super().__init__(task="regression")

    def tune_and_fit(self, X_train: pd.DataFrame, y_train: pd.Series) -> tuple[xgb.Booster, dict]:
        search = RandomizedSearchCV(
            xgb.XGBRegressor(objective="reg:squarederror", n_jobs=1, **_xgb_estimator_kwargs()),
            param_distributions=_XGB_PARAM_DISTRIBUTIONS,
            n_iter=_XGB_SEARCH_ITERATIONS,
            scoring="neg_root_mean_squared_error",
            cv=TimeSeriesSplit(n_splits=_XGB_CV_SPLITS),
            random_state=_XGB_RANDOM_STATE,
            verbose=10,
            n_jobs=_xgb_search_n_jobs(),
        )
        search.fit(X_train, y_train)
        model = xgb.XGBRegressor(objective="reg:squarederror", **search.best_params_, **_xgb_estimator_kwargs())
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
    """A classification-only comparison candidate for win-probability-type
    targets -- promoted from a standalone, never-scheduled script
    (train_baseline_model.py) into a real candidate that can win and reach
    production. Unlike XGBoost, scikit-learn's LogisticRegression can't
    handle NaN or wildly different feature scales natively -- rolling-
    average columns range from percentages (0-1) to yards (0-500) to
    travel km (0-10000), and L1/L2 regularization penalizes coefficient
    magnitude, so an unscaled feature gets penalized unevenly just for
    having a bigger raw scale. Median imputation and standardization are
    both steps in the pipeline itself, fit only on the training slice --
    same leakage rule as the chronological split -- so the fitted
    Pipeline can run directly on raw (NaN-containing) X at predict time,
    same as XGBoost's native missing-value handling."""

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
        # Strips the pipeline step prefix ("model__C" -> "C") -- that
        # prefix is an implementation detail of _build_pipeline's step
        # name, not something worth exposing on the model card.
        best_params = {key.removeprefix("model__"): value for key, value in search.best_params_.items()}
        model = self._build_pipeline().set_params(**{f"model__{k}": v for k, v in best_params.items()})
        model.fit(X_train, y_train)
        return model, best_params

    def predict(self, estimator: Pipeline, X: pd.DataFrame) -> np.ndarray:
        return estimator.predict_proba(X)[:, 1]

    def feature_importances(self, estimator: Pipeline, feature_columns: list[str]) -> dict[str, float]:
        # Standardized coefficients, not XGBoost-style gain -- signed
        # (positive raises the predicted probability, negative lowers it)
        # and comparable across features precisely because every input was
        # standardized to the same scale first.
        coefficients = estimator.named_steps["model"].coef_[0]
        return dict(sorted(
            zip(feature_columns, (float(c) for c in coefficients)),
            key=lambda kv: abs(kv[1]), reverse=True,
        ))


# Regression-only counterpart to LogisticRegressionAdapter -- same
# regularized-linear-model family and imputation/scaling reasoning, split
# by task the same way xgboost's two subclasses are (ElasticNet has no
# classification form the way LogisticRegression has no regression form,
# so there's no shared base to factor out here beyond
# _JoblibSerializedAdapter).
# alpha at 3 points per decade (not 1) -- ElasticNet's loss surface is
# often more sensitive to alpha than 5 sparse log-spaced points can
# resolve, and a fit is cheap enough that filling in the decades costs
# little. l1_ratio adds points near its 1.0 (pure Lasso) end specifically
# -- behavior there is often qualitatively different from the interior,
# not just a smooth continuation of it.
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


# Bagged trees -- a different bias/variance tradeoff than XGBoost's
# boosting (each tree fit independently on a bootstrap resample, then
# averaged/voted, rather than each tree correcting the last one's
# residuals). Chosen specifically for being harder to overfit than
# boosting on a currently-small dataset while still being a real tabular-
# data workhorse, not picked just to fill out the roster. Shared base
# covers everything but predict() (predict_proba only exists on the
# classifier) and algorithm/estimator class identity -- same pattern
# XGBoostAdapter's own classifier/regressor split already established,
# just without XGBoost's lucky "raw output already IS the right value
# either way" property, since a plain sklearn classifier/regressor
# genuinely has different methods for the two.
_RF_PARAM_DISTRIBUTIONS = {
    "model__n_estimators": [100, 200, 300, 400, 500, 600],
    "model__max_depth": [4, 6, 8, 10, 15, 20, None],
    "model__min_samples_leaf": [1, 2, 4, 8],
    "model__max_features": ["sqrt", "log2", None],
}
# Fewer than XGBoost's 400 -- an individual Random Forest fit is usually
# cheap, but full grid coverage matters less for a bagging method whose
# individual trees are already low-bias by construction (unlike a
# boosting method, where each hyperparameter combination materially
# changes what gets learned at every step). Raised alongside the wider
# n_estimators/max_depth ranges to hold roughly the same sample density
# (iterations / combinations) as before.
_RF_SEARCH_ITERATIONS = 70
_RF_CV_SPLITS = 8
_RF_RANDOM_STATE = 42


class _RandomForestAdapterBase(_JoblibSerializedAdapter):
    artifact_filename = "model.joblib"
    _estimator_cls: type = None
    _scoring: str = None

    def _build_pipeline(self) -> Pipeline:
        # No StandardScaler -- trees split on raw thresholds, so feature
        # scale doesn't affect what a Random Forest learns, unlike the
        # linear/neural adapters above and below. SimpleImputer is still
        # needed -- unlike XGBoost, scikit-learn's tree ensembles don't
        # handle NaN natively.
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


# A small neural net -- deliberately the one candidate in this roster
# chosen for getting BETTER as the training set grows toward its eventual
# 15-year size, unlike SVM/KNN (left out of this roster entirely): both
# scale badly in either training cost or serialized-artifact size as row
# count grows, exactly the wrong property for a dataset on a growth
# trajectory. Kept deliberately small (max 64 units) and regularized
# (early_stopping, a real alpha search) since the CURRENT dataset is still
# small enough that a bigger network would just overfit -- this is a
# reasonable starting capacity, not a claim that it's the right size
# forever.
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
        best_params = {key.removeprefix("model__"): value for key, value in search.best_params_.items()}
        model = self._build_pipeline().set_params(**{f"model__{k}": v for k, v in best_params.items()})
        model.fit(X_train, y_train)
        return model, best_params

    def feature_importances(self, estimator: Pipeline, feature_columns: list[str]) -> dict[str, float]:
        # MLPs have no native feature-importance concept the way trees or
        # linear coefficients do -- permutation importance would need a
        # held-out set threaded through here, changing this method's
        # contract for every other adapter, just for this one algorithm.
        # An empty dict is honest about that instead of fabricating a
        # number -- handler.py's _load_model_summary already treats "no
        # top features" as a normal, displayable state.
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


# Keyed by model_card["algorithm"] -- serving (model_loader.py) dispatches
# a promoted model card's own "algorithm" field through this registry to
# deserialize/predict it, without needing to know in advance which
# algorithm happens to be currently promoted for a given target. Only
# needs to cover predict/serialize/deserialize/feature_importances (every
# entry here is safe to use without ever calling tune_and_fit) -- training
# scripts import the classifier/regressor-specific classes directly to
# build their own candidate lists instead of going through this registry,
# since they need to state which task a target is. XGBoostAdapter's own
# predict/serialize/deserialize happen to be identical regardless of
# task, so one shared instance covers both here -- every other algorithm
# needs a distinct registry entry per task (random_forest_classifier vs.
# random_forest_regressor, mlp_classifier vs. mlp_regressor), since a
# plain scikit-learn classifier/regressor pair genuinely has different
# predict methods (predict_proba only exists on the classifier), unlike
# XGBoost's raw Booster output.
#
# 5 algorithm families (xgboost, logistic regression, elastic net, random
# forest, mlp), 7 registry entries -- see model-training/nfl/
# train_win_probability_model.py's CANDIDATES and train_score_model.py/
# train_player_prop_model.py's own candidate lists for which ones actually
# compete for which target; not every entry here is necessarily in every
# target's candidate list.
ADAPTERS: dict[str, ModelAdapter] = {
    "xgboost": XGBoostAdapter(),
    "logistic_regression": LogisticRegressionAdapter(),
    "elastic_net": ElasticNetAdapter(),
    "random_forest_classifier": RandomForestClassifierAdapter(),
    "random_forest_regressor": RandomForestRegressorAdapter(),
    "mlp_classifier": MLPClassifierAdapter(),
    "mlp_regressor": MLPRegressorAdapter(),
}
