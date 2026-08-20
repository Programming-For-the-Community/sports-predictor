"""
Unit tests for the NBA player-prop training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_player_prop_model.py's own orchestration (target-stat filtering,
column selection, naive baseline computation, what gets handed to
run_backtest), not the tournament itself or any real algorithm fitting.
NBA's stat_line has no category-prefixed keys, so there's no
opposing-side machinery to test here.
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_player_prop_model


def _make_df(n=10, target_stat="points", missing_stat_rows=0, games_with_stat=None, avg_stat=None):
    """missing_stat_rows: how many trailing rows record a different stat
    entirely -- must be filtered out rather than crashing or contributing
    a bogus label.

    games_with_stat/avg_stat: per-row values defaulting to comfortably
    clear MIN_PRIOR_GAMES_WITH_STAT/MIN_AVG_FRACTION_OF_MEDIAN so tests
    not exercising those filters don't need to think about them.
    """
    if games_with_stat is None:
        games_with_stat = [5] * n
    if avg_stat is None:
        avg_stat = [25.0 + i for i in range(n)]

    stat_lines = []
    for i in range(n):
        if i >= n - missing_stat_rows:
            stat_lines.append(json.dumps({"blocks": 1}))
        else:
            stat_lines.append(json.dumps({target_stat: 20.0 + i}))

    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "player_key": [f"P{i}" for i in range(n)],
        "entity_id": ["P1"] * n,
        "team_id": ["2"] * n,
        "event_date": [f"2025-12-{i + 1:02d}" for i in range(n)],
        f"avg_{target_stat}": avg_stat,
        "games_played": [i for i in range(n)],
        f"games_with_{target_stat}": games_with_stat,
        "label_stat_line": stat_lines,
        "label_started": [True] * n,
    })


def _fake_result(model_name="player-prop-points", version=1):
    return {
        "promotions": [{"model_name": model_name, "algorithm": "xgboost", "version": version, "rmse": 5.0}],
        "candidates": [{"algorithm": "xgboost", "rmse": 5.0}],
    }


class TestModelName:
    def test_hyphenates_the_stat_name(self):
        assert train_player_prop_model._model_name("three_pointers_made") == "player-prop-three-pointers-made"


class TestFilterToTargetStat:
    def test_keeps_only_rows_with_the_target_stat(self):
        df = _make_df(10, target_stat="points", missing_stat_rows=3)

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        assert len(filtered) == 7

    def test_extracts_the_stat_value_as_a_numeric_label(self):
        df = _make_df(5, target_stat="points")

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        assert list(filtered[train_player_prop_model.LABEL_COLUMN]) == [20.0, 21.0, 22.0, 23.0, 24.0]

    def test_label_column_never_leaks_into_feature_columns(self):
        df = _make_df(5, target_stat="points")
        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        columns = train_player_prop_model._feature_columns(filtered)

        assert train_player_prop_model.LABEL_COLUMN not in columns
        assert "label_stat_line" not in columns
        assert "label_started" not in columns

    def test_excludes_a_one_off_garbage_time_stat_despite_having_it_this_game(self):
        games_with_stat = [5] * 9 + [0]
        df = _make_df(10, target_stat="points", games_with_stat=games_with_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        assert len(filtered) == 9
        assert 0 not in filtered["games_with_points"].values

    def test_requires_at_least_the_minimum_not_strictly_more(self):
        games_with_stat = [train_player_prop_model.MIN_PRIOR_GAMES_WITH_STAT] * 10
        df = _make_df(10, target_stat="points", games_with_stat=games_with_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        assert len(filtered) == 10

    def test_excludes_a_low_volume_recurring_bench_player_relative_to_peers(self):
        # Four rows near a real rotation player's average (25), one row
        # from a deep-bench player whose own average (2) is far below
        # their peers despite clearing games_with_<stat>.
        avg_stat = [25.0, 25.0, 25.0, 25.0, 2.0]
        df = _make_df(5, target_stat="points", games_with_stat=[5] * 5, avg_stat=avg_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        assert len(filtered) == 4
        assert 2.0 not in filtered["avg_points"].values

    def test_keeps_a_player_at_exactly_the_fraction_of_median(self):
        # median([25,25,25,25,8.75]) is 25; 0.35 * 25 = 8.75 exactly.
        avg_stat = [25.0, 25.0, 25.0, 25.0, 8.75]
        df = _make_df(5, target_stat="points", games_with_stat=[5] * 5, avg_stat=avg_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        assert len(filtered) == 5

    def test_median_is_computed_after_the_volume_filter_not_before(self):
        games_with_stat = [5, 5, 5, 5, 0]
        avg_stat = [25.0, 25.0, 25.0, 5.0, 1.0]
        df = _make_df(5, target_stat="points", games_with_stat=games_with_stat, avg_stat=avg_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "points")

        # 4 real rows remain after the volume filter: [25,25,25,5] ->
        # median 25, 0.35*25=8.75 > 5, so the 5.0 row is also excluded.
        assert len(filtered) == 3
        assert 5.0 not in filtered["avg_points"].values


class TestFeatureColumns:
    def test_excludes_identifiers(self):
        df = _make_df(5)

        columns = train_player_prop_model._feature_columns(df)

        assert "event_key" not in columns
        assert "player_key" not in columns
        assert "entity_id" not in columns
        assert "team_id" not in columns
        assert "opponent_id" not in columns
        assert "event_date" not in columns

    def test_drops_columns_that_are_entirely_null(self):
        df = _make_df(5)
        df["avg_blocks"] = [None] * 5

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_blocks" not in columns
        assert "avg_points" in columns

    def test_drops_a_column_below_the_non_null_fraction(self):
        df = _make_df(100)
        df["avg_steals"] = [None] * 99 + [1.0]

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_steals" not in columns

    def test_keeps_a_column_at_or_above_the_non_null_fraction(self):
        df = _make_df(100)
        df["avg_assists"] = [None] * 90 + [5.0] * 10  # exactly 10%

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_assists" in columns

    def test_no_opposing_side_exclusion_a_column_common_to_every_position_survives(self):
        # NBA has no offense/defense category split, so a column survives
        # here purely on MIN_NON_NULL_FRACTION.
        df = _make_df(100, target_stat="points")
        df["avg_rebounds"] = [None] * 80 + [8.0] * 20

        columns = train_player_prop_model._feature_columns(df)

        assert "avg_rebounds" in columns


class TestTrain:
    def test_calls_run_backtest_with_regression_task_and_five_candidates(self):
        df = _make_df(10, target_stat="points")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_player_prop_model.train(MagicMock(), df, "points")

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert call.kwargs["candidates"] == train_player_prop_model.CANDIDATES
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter", "RandomForestRegressorAdapter",
            "MLPRegressorAdapter", "LightGBMRegressorAdapter",
        }
        assert result == _fake_result()

    def test_uses_the_stats_own_model_name(self):
        df = _make_df(10, target_stat="points")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "points")

        assert mock_run.call_args.args[2] == "player-prop-points"

    def test_filters_to_target_stat_before_splitting(self):
        df = _make_df(10, target_stat="points", missing_stat_rows=3)

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "points")

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 7

    def test_includes_target_stat_in_extra_metadata(self):
        df = _make_df(10, target_stat="points")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "points")

        assert mock_run.call_args.kwargs["extra_metadata"]["target_stat"] == "points"

    def test_naive_baseline_predicts_the_players_own_rolling_average(self):
        n = 10
        df = _make_df(n, target_stat="points", avg_stat=[20.0 + i for i in range(n)])

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "points")

        naive = mock_run.call_args.kwargs["naive_baseline_metrics"]
        assert naive["naive_baseline_rmse"] == pytest.approx(0.0)
        assert naive["naive_baseline_mae"] == pytest.approx(0.0)

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10, target_stat="points")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "points")

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
    def test_requires_target_stat_env_var(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.delenv("TARGET_STAT", raising=False)

        with pytest.raises(KeyError):
            train_player_prop_model.main()

    def test_requires_bucket_env_var(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACTS_BUCKET_NAME", raising=False)
        monkeypatch.setenv("TARGET_STAT", "points")

        with pytest.raises(KeyError):
            train_player_prop_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("TARGET_STAT", "points")
        df = _make_df(10, target_stat="points")
        mock_s3 = MagicMock()

        with patch.object(train_player_prop_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_player_prop_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_player_prop_model, "train", return_value=_fake_result()) as mock_train:
            train_player_prop_model.main()

        mock_load.assert_called_once_with(mock_s3, train_player_prop_model.PLAYER_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df, "points")
