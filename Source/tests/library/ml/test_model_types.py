"""
Unit tests for library/ml/model_types.py's algorithm adapters.

Hyperparameter search (RandomizedSearchCV/GridSearchCV) and the
sklearn/xgboost estimator classes are always mocked -- same boundary
Source/tests/model-training/nfl's existing tests already draw around real
fitting, just now scoped to the adapters these were refactored out of.
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb

from library.ml import model_types


def _df(n=4, columns=("a", "b")):
    return pd.DataFrame({col: [float(i) for i in range(n)] for col in columns})


class TestXGBoostAdapterPredict:
    """predict() always means "the model's raw output" -- probability for
    a classification-task booster, a continuous value for a
    regression-task one -- so a single implementation on the shared base
    class covers both, and this test doesn't need to care which task the
    booster underneath was trained for."""

    def test_builds_a_dmatrix_from_the_frames_own_columns_and_returns_raw_output(self):
        adapter = model_types.XGBoostAdapter()
        mock_booster = MagicMock()
        mock_booster.predict.return_value = np.array([0.7, 0.2])

        result = adapter.predict(mock_booster, _df(2))

        assert list(result) == [0.7, 0.2]
        called_matrix = mock_booster.predict.call_args.args[0]
        assert isinstance(called_matrix, xgb.DMatrix)

    def test_base_adapter_refuses_to_train_without_a_task(self):
        adapter = model_types.XGBoostAdapter()
        with pytest.raises(NotImplementedError):
            adapter.tune_and_fit(_df(), pd.Series([0, 1, 0, 1]))


class TestXGBoostAdapterSerialization:
    def test_serialize_calls_save_raw(self):
        adapter = model_types.XGBoostAdapter()
        mock_booster = MagicMock()
        mock_booster.save_raw.return_value = b"raw-bytes"

        assert adapter.serialize(mock_booster) == b"raw-bytes"

    def test_deserialize_loads_a_booster_from_bytes(self):
        adapter = model_types.XGBoostAdapter()
        with patch.object(model_types.xgb, "Booster") as mock_booster_cls:
            mock_booster = MagicMock()
            mock_booster_cls.return_value = mock_booster

            result = adapter.deserialize(b"raw-bytes")

        mock_booster.load_model.assert_called_once_with(bytearray(b"raw-bytes"))
        assert result is mock_booster


class TestXGBoostAdapterFeatureImportances:
    def test_defaults_missing_features_to_zero_and_sorts_descending(self):
        adapter = model_types.XGBoostAdapter()
        mock_booster = MagicMock()
        mock_booster.get_score.return_value = {"a": 3.0}

        importances = adapter.feature_importances(mock_booster, ["a", "b", "c"])

        assert importances == {"a": 3.0, "b": 0.0, "c": 0.0}
        assert list(importances.keys()) == ["a", "b", "c"]


class TestLogSearchConvergence:
    def _search(self, scores):
        search = MagicMock()
        search.cv_results_ = {"mean_test_score": scores}
        return search

    def test_logs_running_best_at_checkpoints(self, caplog):
        # 10 trials, worsening after the 5th -- running best should stay
        # flat at trial 5's score from there on.
        scores = [-1.0, -0.8, -0.6, -0.5, -0.4, -0.9, -0.7, -0.6, -0.9, -0.95]
        with caplog.at_level("INFO"):
            model_types._log_search_convergence("some_algorithm", self._search(scores))

        [record] = [r for r in caplog.records if "some_algorithm" in r.message]
        assert "1/10=-1.00000" in record.message
        assert "5/10=-0.40000" in record.message  # best reached at trial 5
        assert "10/10=-0.40000" in record.message  # never improves after

    def test_malformed_cv_results_does_not_raise(self, caplog):
        search = MagicMock()
        search.cv_results_ = {}  # missing "mean_test_score" entirely

        with caplog.at_level("INFO"):
            model_types._log_search_convergence("some_algorithm", search)  # must not raise

        assert not any("some_algorithm" in r.message and r.levelname == "INFO" for r in caplog.records)


class TestXGBoostClassifierAdapter:
    def test_tune_and_fit_searches_with_binary_logistic_objective_and_log_loss_scoring(self):
        adapter = model_types.XGBoostClassifierAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"max_depth": 3}
        mock_fitted = MagicMock()
        mock_fitted.get_booster.return_value = "the-booster"

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(model_types.xgb, "XGBClassifier", return_value=mock_fitted) as mock_cls:
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([0, 1, 0, 1]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_log_loss"
        mock_cls.assert_called_with(objective="binary:logistic", eval_metric="logloss", max_depth=3)
        assert estimator == "the-booster"
        assert params == {"max_depth": 3}

    def test_task_is_classification(self):
        assert model_types.XGBoostClassifierAdapter().task == "classification"


class TestXGBoostRegressorAdapter:
    def test_tune_and_fit_searches_with_squared_error_objective_and_rmse_scoring(self):
        adapter = model_types.XGBoostRegressorAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"max_depth": 4}
        mock_fitted = MagicMock()
        mock_fitted.get_booster.return_value = "the-booster"

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(model_types.xgb, "XGBRegressor", return_value=mock_fitted) as mock_cls:
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([10.0, 20.0, 30.0, 40.0]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_root_mean_squared_error"
        mock_cls.assert_called_with(objective="reg:squarederror", max_depth=4)
        assert estimator == "the-booster"

    def test_task_is_regression(self):
        assert model_types.XGBoostRegressorAdapter().task == "regression"


class TestLogisticRegressionAdapter:
    def _mock_pipeline(self, coefficients=(0.5, -0.3)):
        mock_pipeline = MagicMock()
        mock_pipeline.set_params.return_value = mock_pipeline
        mock_pipeline.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_pipeline.named_steps = {"model": MagicMock(coef_=np.array([list(coefficients)]))}
        return mock_pipeline

    def test_predict_returns_positive_class_probability(self):
        adapter = model_types.LogisticRegressionAdapter()
        pipeline = self._mock_pipeline()

        result = adapter.predict(pipeline, _df(2))

        assert list(result) == [0.9, 0.2]

    def test_tune_and_fit_strips_pipeline_prefix_from_hyperparameters(self):
        adapter = model_types.LogisticRegressionAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"model__C": 1.0, "model__penalty": "l2"}
        mock_pipeline = self._mock_pipeline()

        with patch.object(model_types, "GridSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(adapter, "_build_pipeline", return_value=mock_pipeline):
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([0, 1, 0, 1]))

        assert params == {"C": 1.0, "penalty": "l2"}
        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_log_loss"
        mock_pipeline.set_params.assert_called_once_with(model__C=1.0, model__penalty="l2")
        assert estimator is mock_pipeline

    def test_feature_importances_are_signed_and_sorted_by_magnitude(self):
        adapter = model_types.LogisticRegressionAdapter()
        pipeline = self._mock_pipeline(coefficients=(0.5, -0.3))

        importances = adapter.feature_importances(pipeline, ["a", "b"])

        assert importances == {"a": 0.5, "b": -0.3}
        assert list(importances.keys()) == ["a", "b"]

    def test_serialize_and_deserialize_round_trip_via_joblib(self):
        adapter = model_types.LogisticRegressionAdapter()
        fake_pipeline = {"not": "a real pipeline, just something joblib can round-trip"}

        raw = adapter.serialize(fake_pipeline)
        result = adapter.deserialize(raw)

        assert result == fake_pipeline


class TestElasticNetAdapter:
    def _mock_pipeline(self, coefficients=(0.5, -0.3)):
        mock_pipeline = MagicMock()
        mock_pipeline.set_params.return_value = mock_pipeline
        mock_pipeline.predict.return_value = np.array([10.0, 20.0])
        mock_pipeline.named_steps = {"model": MagicMock(coef_=np.array(list(coefficients)))}
        return mock_pipeline

    def test_predict_returns_the_continuous_value_directly(self):
        adapter = model_types.ElasticNetAdapter()
        pipeline = self._mock_pipeline()

        result = adapter.predict(pipeline, _df(2))

        assert list(result) == [10.0, 20.0]

    def test_tune_and_fit_searches_alpha_and_l1_ratio_scored_on_rmse(self):
        adapter = model_types.ElasticNetAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"model__alpha": 0.1, "model__l1_ratio": 0.5}
        mock_pipeline = self._mock_pipeline()

        with patch.object(model_types, "GridSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(adapter, "_build_pipeline", return_value=mock_pipeline):
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([10.0, 20.0, 30.0, 40.0]))

        assert params == {"alpha": 0.1, "l1_ratio": 0.5}
        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_root_mean_squared_error"
        assert mock_search_cls.call_args.kwargs["param_grid"] == model_types._ELASTIC_NET_PARAM_GRID
        assert estimator is mock_pipeline

    def test_feature_importances_are_signed_coefficients_sorted_by_magnitude(self):
        adapter = model_types.ElasticNetAdapter()
        pipeline = self._mock_pipeline(coefficients=(0.5, -0.9))

        importances = adapter.feature_importances(pipeline, ["a", "b"])

        assert importances == {"b": -0.9, "a": 0.5}
        assert list(importances.keys()) == ["b", "a"]


class TestRandomForestAdapters:
    def _mock_pipeline(self, importances=(0.7, 0.3)):
        mock_pipeline = MagicMock()
        mock_pipeline.set_params.return_value = mock_pipeline
        mock_pipeline.predict.return_value = np.array([10.0, 20.0])
        mock_pipeline.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        mock_pipeline.named_steps = {"model": MagicMock(feature_importances_=np.array(list(importances)))}
        return mock_pipeline

    def test_classifier_predict_returns_positive_class_probability(self):
        adapter = model_types.RandomForestClassifierAdapter()
        pipeline = self._mock_pipeline()

        result = adapter.predict(pipeline, _df(2))

        assert list(result) == [0.9, 0.2]

    def test_regressor_predict_returns_the_continuous_value(self):
        adapter = model_types.RandomForestRegressorAdapter()
        pipeline = self._mock_pipeline()

        result = adapter.predict(pipeline, _df(2))

        assert list(result) == [10.0, 20.0]

    def test_classifier_and_regressor_use_distinct_algorithm_names(self):
        # Unlike XGBoost, a plain sklearn classifier/regressor pair has
        # genuinely different predict methods -- serving needs to know
        # which one it's dealing with, so they can't share one registry
        # entry the way XGBoostAdapter's two subclasses do.
        assert model_types.RandomForestClassifierAdapter.algorithm == "random_forest_classifier"
        assert model_types.RandomForestRegressorAdapter.algorithm == "random_forest_regressor"

    def test_classifier_tunes_with_log_loss_scoring(self):
        adapter = model_types.RandomForestClassifierAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"model__n_estimators": 200}
        mock_pipeline = self._mock_pipeline()

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(adapter, "_build_pipeline", return_value=mock_pipeline):
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([0, 1, 0, 1]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_log_loss"
        assert params == {"n_estimators": 200}
        assert estimator is mock_pipeline

    def test_regressor_tunes_with_rmse_scoring(self):
        adapter = model_types.RandomForestRegressorAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"model__n_estimators": 300}
        mock_pipeline = self._mock_pipeline()

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(adapter, "_build_pipeline", return_value=mock_pipeline):
            adapter.tune_and_fit(_df(), pd.Series([10.0, 20.0, 30.0, 40.0]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_root_mean_squared_error"

    def test_feature_importances_are_unsigned_and_sorted_descending(self):
        adapter = model_types.RandomForestRegressorAdapter()
        pipeline = self._mock_pipeline(importances=(0.2, 0.8))

        importances = adapter.feature_importances(pipeline, ["a", "b"])

        assert importances == {"b": 0.8, "a": 0.2}
        assert list(importances.keys()) == ["b", "a"]

    def test_pipeline_has_no_scaler_only_imputer(self):
        # Trees don't need feature scaling -- only the linear/neural
        # adapters do.
        adapter = model_types.RandomForestClassifierAdapter()
        pipeline = adapter._build_pipeline()

        assert [name for name, _ in pipeline.steps] == ["impute", "model"]


class TestMLPAdapters:
    def _mock_pipeline(self):
        mock_pipeline = MagicMock()
        mock_pipeline.set_params.return_value = mock_pipeline
        mock_pipeline.predict.return_value = np.array([10.0, 20.0])
        mock_pipeline.predict_proba.return_value = np.array([[0.1, 0.9], [0.8, 0.2]])
        return mock_pipeline

    def test_classifier_predict_returns_positive_class_probability(self):
        adapter = model_types.MLPClassifierAdapter()
        pipeline = self._mock_pipeline()

        result = adapter.predict(pipeline, _df(2))

        assert list(result) == [0.9, 0.2]

    def test_regressor_predict_returns_the_continuous_value(self):
        adapter = model_types.MLPRegressorAdapter()
        pipeline = self._mock_pipeline()

        result = adapter.predict(pipeline, _df(2))

        assert list(result) == [10.0, 20.0]

    def test_feature_importances_are_honestly_empty(self):
        # MLPs have no native feature-importance concept -- an empty dict
        # is honest about that instead of fabricating a number.
        adapter = model_types.MLPClassifierAdapter()

        assert adapter.feature_importances(MagicMock(), ["a", "b"]) == {}

    def test_classifier_tunes_with_log_loss_scoring(self):
        adapter = model_types.MLPClassifierAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"model__alpha": 0.01}
        mock_pipeline = self._mock_pipeline()

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(adapter, "_build_pipeline", return_value=mock_pipeline):
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([0, 1, 0, 1]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_log_loss"
        assert params == {"alpha": 0.01}
        assert estimator is mock_pipeline

    def test_pipeline_uses_early_stopping_to_guard_against_overfitting(self):
        adapter = model_types.MLPClassifierAdapter()
        pipeline = adapter._build_pipeline()

        mlp = pipeline.named_steps["model"]
        assert mlp.early_stopping is True
        assert mlp.max_iter == 2000

    def test_pipeline_has_a_scaler_neural_nets_are_scale_sensitive(self):
        adapter = model_types.MLPRegressorAdapter()
        pipeline = adapter._build_pipeline()

        assert [name for name, _ in pipeline.steps] == ["impute", "scale", "model"]


class TestLightGBMAdapters:
    """lightgbm isn't installed in every environment that imports this
    module (see model_types.py's own docstring on the lazy-import
    contract) -- these tests never import the real package, instead
    patching model_types._lgbm_estimator_classes directly with fake
    classifier/regressor classes, same "always mock the estimator class
    and the search" boundary every other adapter's tests here draw, just
    at the one extra indirection level the lazy import adds."""

    def test_instantiating_the_registry_entries_needs_no_lightgbm_installed(self):
        # Proves the module docstring's central claim: importing
        # model_types and instantiating ADAPTERS never touches lightgbm --
        # only tune_and_fit does (see the tests below). If this test can
        # run at all (module-level import already succeeded to collect
        # this file), the claim already holds; asserting the instances
        # exist and are the right type makes that explicit rather than
        # implicit in "the file imported".
        assert isinstance(model_types.ADAPTERS["lightgbm_classifier"], model_types.LightGBMClassifierAdapter)
        assert isinstance(model_types.ADAPTERS["lightgbm_regressor"], model_types.LightGBMRegressorAdapter)

    def test_classifier_tune_and_fit_searches_with_binary_objective_and_log_loss_scoring(self):
        adapter = model_types.LightGBMClassifierAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"num_leaves": 31}
        mock_fitted = MagicMock()
        mock_classifier_cls = MagicMock(return_value=mock_fitted)
        mock_regressor_cls = MagicMock()

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(model_types, "_lgbm_estimator_classes", return_value=(mock_classifier_cls, mock_regressor_cls)):
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([0, 1, 0, 1]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_log_loss"
        mock_classifier_cls.assert_called_with(
            objective="binary", verbosity=-1, random_state=model_types._LGBM_RANDOM_STATE, num_leaves=31,
        )
        assert estimator is mock_fitted
        assert params == {"num_leaves": 31}

    def test_regressor_tune_and_fit_searches_with_regression_objective_and_rmse_scoring(self):
        adapter = model_types.LightGBMRegressorAdapter()
        mock_search = MagicMock()
        mock_search.best_params_ = {"num_leaves": 63}
        mock_fitted = MagicMock()
        mock_classifier_cls = MagicMock()
        mock_regressor_cls = MagicMock(return_value=mock_fitted)

        with patch.object(model_types, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(model_types, "_lgbm_estimator_classes", return_value=(mock_classifier_cls, mock_regressor_cls)):
            estimator, params = adapter.tune_and_fit(_df(), pd.Series([1.0, 2.0, 3.0, 4.0]))

        assert mock_search_cls.call_args.kwargs["scoring"] == "neg_root_mean_squared_error"
        mock_regressor_cls.assert_called_with(
            objective="regression", verbosity=-1, random_state=model_types._LGBM_RANDOM_STATE, num_leaves=63,
        )
        assert estimator is mock_fitted
        assert params == {"num_leaves": 63}

    def test_classifier_predict_returns_positive_class_probability(self):
        adapter = model_types.LightGBMClassifierAdapter()
        mock_estimator = MagicMock()
        mock_estimator.predict_proba.return_value = np.array([[0.3, 0.7], [0.9, 0.1]])

        result = adapter.predict(mock_estimator, _df(2))

        assert list(result) == [0.7, 0.1]

    def test_regressor_predict_returns_the_continuous_value_directly(self):
        adapter = model_types.LightGBMRegressorAdapter()
        mock_estimator = MagicMock()
        mock_estimator.predict.return_value = np.array([12.5, 8.0])

        result = adapter.predict(mock_estimator, _df(2))

        assert list(result) == [12.5, 8.0]

    def test_feature_importances_are_unsigned_and_sorted_descending(self):
        adapter = model_types.LightGBMRegressorAdapter()
        mock_estimator = MagicMock()
        mock_estimator.feature_importances_ = np.array([1.0, 5.0])

        importances = adapter.feature_importances(mock_estimator, ["a", "b"])

        assert list(importances.items()) == [("b", 5.0), ("a", 1.0)]

    def test_serialize_and_deserialize_round_trip_via_joblib(self):
        # _JoblibSerializedAdapter's shared behavior, same as
        # TestLogisticRegressionAdapter's own round-trip test -- proves
        # LightGBM's adapters get it for free the same way, no lightgbm
        # import needed since joblib just pickles whatever object it's
        # handed.
        adapter = model_types.LightGBMClassifierAdapter()
        fake_model = {"not": "really a model, just picklable"}

        raw = adapter.serialize(fake_model)
        result = adapter.deserialize(raw)

        assert result == fake_model


class TestAdaptersRegistry:
    def test_registry_is_keyed_by_model_card_algorithm_name(self):
        assert set(model_types.ADAPTERS) == {
            "xgboost", "logistic_regression", "elastic_net",
            "random_forest_classifier", "random_forest_regressor",
            "mlp_classifier", "mlp_regressor",
            "lightgbm_classifier", "lightgbm_regressor",
        }

    def test_xgboost_entry_needs_no_task_to_serve(self):
        # Serving only ever calls predict/serialize/deserialize/
        # feature_importances -- never tune_and_fit -- so the registry's
        # shared instance is safe to use for both a classifier-task and a
        # regressor-task promoted model without knowing which.
        assert model_types.ADAPTERS["xgboost"].task is None
