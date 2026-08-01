"""
Unit tests for the NFL player-prop training entrypoint. xgboost's
XGBRegressor is ALWAYS mocked here, same mocking boundary as
test_train_model.py draws around XGBClassifier -- these tests verify
train_player_prop_model.py's own orchestration (target-stat filtering,
column selection, metric computation, versioned S3 writes), never a real
fit.
"""
import io
import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import train_player_prop_model


def _make_df(n=10, target_stat="passing_yards", missing_stat_rows=0, games_with_stat=None, avg_stat=None):
    """missing_stat_rows: how many trailing rows record a different stat
    entirely (e.g. a kicker's field_goals_made) -- these must be filtered
    out rather than crashing or contributing a bogus label.

    games_with_stat: per-row games_with_<target_stat> values, defaulting
    to comfortably clearing MIN_PRIOR_GAMES_WITH_STAT so tests that
    aren't specifically exercising the volume filter don't need to think
    about it.

    avg_stat: per-row avg_<target_stat> values, defaulting to a range
    comfortably clearing MIN_AVG_FRACTION_OF_MEDIAN of their own median
    so tests that aren't specifically exercising the magnitude filter
    don't need to think about it.
    """
    if games_with_stat is None:
        games_with_stat = [5] * n
    if avg_stat is None:
        avg_stat = [250.0 + i for i in range(n)]

    stat_lines = []
    for i in range(n):
        if i >= n - missing_stat_rows:
            stat_lines.append(json.dumps({"field_goals_made": 2}))
        else:
            stat_lines.append(json.dumps({target_stat: 200.0 + i}))

    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "player_key": [f"P{i}" for i in range(n)],
        "entity_id": ["QB1"] * n,
        "team_id": ["KC"] * n,
        "event_date": [f"2025-09-{i + 1:02d}" for i in range(n)],
        f"avg_{target_stat}": avg_stat,
        "games_played": [i for i in range(n)],
        f"games_with_{target_stat}": games_with_stat,
        "label_stat_line": stat_lines,
        "label_started": [True] * n,
    })


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False)
    return buffer.getvalue()


class TestModelName:
    def test_hyphenates_the_stat_name(self):
        assert train_player_prop_model._model_name("passing_yards") == "player-prop-passing-yards"


class TestFilterToTargetStat:
    def test_keeps_only_rows_with_the_target_stat(self):
        df = _make_df(10, target_stat="passing_yards", missing_stat_rows=3)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 7

    def test_extracts_the_stat_value_as_a_numeric_label(self):
        df = _make_df(5, target_stat="passing_yards")

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert list(filtered[train_player_prop_model.LABEL_COLUMN]) == [200.0, 201.0, 202.0, 203.0, 204.0]

    def test_label_column_never_leaks_into_feature_columns(self):
        df = _make_df(5, target_stat="passing_yards")
        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        columns = train_player_prop_model._feature_columns(filtered)

        assert train_player_prop_model.LABEL_COLUMN not in columns
        assert "label_stat_line" not in columns
        assert "label_started" not in columns

    def test_excludes_a_one_off_gadget_play_despite_having_the_stat_this_game(self):
        # Has the stat in the label game (has_stat check passes) but no
        # real prior history of it -- exactly the passing-yards v1 model
        # card's contamination scenario (a WR's single career pass).
        games_with_stat = [5] * 9 + [0]
        df = _make_df(10, target_stat="passing_yards", games_with_stat=games_with_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 9
        assert 0 not in filtered["games_with_passing_yards"].values

    def test_requires_at_least_the_minimum_not_strictly_more(self):
        games_with_stat = [train_player_prop_model.MIN_PRIOR_GAMES_WITH_STAT] * 10
        df = _make_df(10, target_stat="passing_yards", games_with_stat=games_with_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 10

    def test_excludes_a_low_volume_recurring_participant_relative_to_peers(self):
        # Four rows near a real starter's typical average (300), one row
        # from a player whose own average (20) is far below their peers
        # despite clearing games_with_<stat> -- a recurring low-volume
        # gadget role, not a real passer. median([300,300,300,300,20]) is
        # 300 (5 values, sorted middle); 0.35 * 300 = 105, well above 20.
        avg_stat = [300.0, 300.0, 300.0, 300.0, 20.0]
        df = _make_df(5, target_stat="passing_yards", games_with_stat=[5] * 5, avg_stat=avg_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 4
        assert 20.0 not in filtered["avg_passing_yards"].values

    def test_keeps_a_player_at_exactly_the_fraction_of_median(self):
        # median([300,300,300,300,105]) is 300; 0.35 * 300 = 105 exactly
        # -- the ">=" boundary must include, not exclude, this row.
        avg_stat = [300.0, 300.0, 300.0, 300.0, 105.0]
        df = _make_df(5, target_stat="passing_yards", games_with_stat=[5] * 5, avg_stat=avg_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 5

    def test_median_is_computed_after_the_volume_filter_not_before(self):
        # Without excluding the games_with_stat=0 row first, its low
        # average (10) would drag the median down and let a genuinely
        # low-volume row (60) slip through the magnitude filter too.
        # median of just the 4 real rows ([300,300,300,60]) is 300;
        # 0.35 * 300 = 105 > 60, so the low row is correctly excluded.
        games_with_stat = [5, 5, 5, 5, 0]
        avg_stat = [300.0, 300.0, 300.0, 60.0, 10.0]
        df = _make_df(5, target_stat="passing_yards", games_with_stat=games_with_stat, avg_stat=avg_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 3
        assert 60.0 not in filtered["avg_passing_yards"].values


class TestFeatureColumns:
    def test_excludes_identifiers(self):
        df = _make_df(5)

        columns = train_player_prop_model._feature_columns(df)

        assert "event_key" not in columns
        assert "player_key" not in columns
        assert "entity_id" not in columns
        assert "team_id" not in columns
        assert "event_date" not in columns

    def test_drops_columns_that_are_entirely_null(self):
        # avg_field_goals_made is a real column in player_features.parquet's
        # shared schema (some kicker somewhere has it), but structurally
        # irrelevant to this all-QB slice -- 100% null here.
        df = _make_df(5)
        df["avg_field_goals_made"] = [None] * 5

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_field_goals_made" not in columns
        assert "avg_passing_yards" in columns

    def test_drops_a_column_below_the_non_null_fraction(self):
        # 1 real value out of 100 rows is 1% -- below MIN_NON_NULL_FRACTION
        # (5%). Needs a large n; a tiny fixture would let even one value
        # trivially clear 5% of the row count.
        df = _make_df(100)
        df["avg_defensive_sacks"] = [None] * 99 + [1.0]

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_defensive_sacks" not in columns

    def test_keeps_a_column_at_or_above_the_non_null_fraction(self):
        df = _make_df(100)
        df["avg_rushing_yards"] = [None] * 90 + [10.0] * 10  # exactly 10%

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_rushing_yards" in columns


class TestTrain:
    def _mock_model(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([250.0, 260.0])
        mock_model.get_booster.return_value.get_score.return_value = {}
        return mock_model

    def test_fits_mocked_model_and_computes_metrics(self):
        df = _make_df(10, target_stat="passing_yards")
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            model, metadata = train_player_prop_model.train(df, "passing_yards")

        mock_model.fit.assert_called_once()
        assert model is mock_model
        assert metadata["target_stat"] == "passing_yards"
        assert set(metadata["feature_columns"]) == {"avg_passing_yards", "games_played", "games_with_passing_yards"}
        assert metadata["train_rows"] == 8
        assert metadata["test_rows"] == 2

    def test_filters_to_target_stat_before_splitting(self):
        # 3 of 10 rows record a different stat -- the 80/20 split must
        # apply to the remaining 7, not the original 10.
        df = _make_df(10, target_stat="passing_yards", missing_stat_rows=3)
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_player_prop_model.train(df, "passing_yards")

        assert metadata["train_rows"] + metadata["test_rows"] == 7

    def test_metrics_are_rmse_and_mae_not_classification_metrics(self):
        df = _make_df(10, target_stat="passing_yards")
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_player_prop_model.train(df, "passing_yards")

        assert isinstance(metadata["rmse"], float)
        assert isinstance(metadata["mae"], float)
        assert "accuracy" not in metadata
        assert "log_loss" not in metadata

    def test_feature_importances_default_unused_features_to_zero(self):
        df = _make_df(10, target_stat="passing_yards")
        mock_model = self._mock_model()
        mock_model.get_booster.return_value.get_score.return_value = {"avg_passing_yards": 5.0}

        with patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_player_prop_model.train(df, "passing_yards")

        assert metadata["feature_importances"]["avg_passing_yards"] == 5.0
        assert metadata["feature_importances"]["games_played"] == 0.0

    def test_includes_naive_baseline_metrics(self):
        df = _make_df(10, target_stat="passing_yards")
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_player_prop_model.train(df, "passing_yards")

        assert isinstance(metadata["naive_baseline_rmse"], float)
        assert isinstance(metadata["naive_baseline_mae"], float)

    def test_naive_baseline_predicts_the_players_own_rolling_average(self):
        # Set avg_passing_yards to exactly match each row's own label
        # (200 + i, from _make_df's stat_line values) -- the naive
        # baseline's error should be exactly zero regardless of what the
        # (mocked) model itself predicts.
        n = 10
        df = _make_df(n, target_stat="passing_yards", avg_stat=[200.0 + i for i in range(n)])
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            _, metadata = train_player_prop_model.train(df, "passing_yards")

        assert metadata["naive_baseline_rmse"] == pytest.approx(0.0)
        assert metadata["naive_baseline_mae"] == pytest.approx(0.0)


class TestTuneHyperparameters:
    def test_uses_time_series_split_and_rmse_scoring(self):
        df = _make_df(10, target_stat="passing_yards")
        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")
        feature_columns = train_player_prop_model._feature_columns(filtered)
        X, y = filtered[feature_columns], filtered[train_player_prop_model.LABEL_COLUMN]
        mock_search = MagicMock()
        mock_search.best_params_ = {"max_depth": 3}
        mock_search.best_score_ = -15.0

        with patch.object(train_player_prop_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls:
            result = train_player_prop_model._tune_hyperparameters(X, y)

        mock_search.fit.assert_called_once()
        call_kwargs = mock_search_cls.call_args.kwargs
        assert isinstance(call_kwargs["cv"], train_player_prop_model.TimeSeriesSplit)
        assert call_kwargs["scoring"] == "neg_root_mean_squared_error"
        assert call_kwargs["param_distributions"] == train_player_prop_model.PARAM_DISTRIBUTIONS
        assert result == {"max_depth": 3}

    def test_parallelizes_search_without_oversubscribing_per_fit_threads(self):
        df = _make_df(10, target_stat="passing_yards")
        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")
        feature_columns = train_player_prop_model._feature_columns(filtered)
        X, y = filtered[feature_columns], filtered[train_player_prop_model.LABEL_COLUMN]
        mock_search = MagicMock()
        mock_search.best_params_ = {}
        mock_search.best_score_ = -15.0

        with patch.object(train_player_prop_model, "RandomizedSearchCV", return_value=mock_search) as mock_search_cls, \
             patch.object(train_player_prop_model.xgb, "XGBRegressor") as mock_xgb_cls:
            train_player_prop_model._tune_hyperparameters(X, y)

        assert mock_search_cls.call_args.kwargs["n_jobs"] == -1
        mock_xgb_cls.assert_called_once_with(objective="reg:squarederror", n_jobs=1)


class TestMain:
    def _mock_model(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([250.0, 260.0])
        mock_model.get_booster.return_value.save_raw.return_value = b"fake-model-bytes"
        mock_model.get_booster.return_value.get_score.return_value = {}
        return mock_model

    def test_writes_versioned_model_under_a_stat_specific_model_name(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("TARGET_STAT", "passing_yards")

        df = _make_df(10, target_stat="passing_yards")
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = []
        mock_s3.object_exists.return_value = False
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model):
            train_player_prop_model.main()

        model_call = mock_s3.put_bytes.call_args
        assert model_call.args[0] == "nfl/player-prop-passing-yards/v1/model.xgb"
        assert model_call.args[1] == b"fake-model-bytes"

    def test_requires_target_stat_env_var(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.delenv("TARGET_STAT", raising=False)

        with pytest.raises(KeyError):
            train_player_prop_model.main()

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)
        monkeypatch.setenv("TARGET_STAT", "passing_yards")

        with pytest.raises(KeyError):
            train_player_prop_model.main()

    def test_gates_promotion_on_rmse_not_log_loss(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("TARGET_STAT", "passing_yards")

        df = _make_df(10, target_stat="passing_yards")
        mock_s3 = MagicMock()
        mock_s3.get_bytes.return_value = _parquet_bytes(df)
        mock_s3.list_keys.return_value = []
        mock_s3.object_exists.return_value = False
        mock_model = self._mock_model()

        with patch.object(train_player_prop_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_player_prop_model, "_tune_hyperparameters", return_value={}), \
             patch.object(train_player_prop_model.xgb, "XGBRegressor", return_value=mock_model), \
             patch.object(train_player_prop_model.model_common, "promote_if_better") as mock_promote:
            train_player_prop_model.main()

        assert mock_promote.call_args.args[-1] == "rmse"
