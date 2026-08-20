"""
Unit tests for the NCAAFB player-prop training entrypoint. CFBD's own
category set has no separate "interceptions" category, so
DEFENSIVE_CATEGORIES here is just {"defensive"}.
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_player_prop_model


def _make_df(n=10, target_stat="passing_yards", missing_stat_rows=0, games_with_stat=None, avg_stat=None):
    if games_with_stat is None:
        games_with_stat = [5] * n
    if avg_stat is None:
        avg_stat = [250.0 + i for i in range(n)]

    stat_lines = []
    for i in range(n):
        if i >= n - missing_stat_rows:
            stat_lines.append(json.dumps({"kicking_field_goals_made": 2}))
        else:
            stat_lines.append(json.dumps({target_stat: 200.0 + i}))

    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "player_key": [f"P{i}" for i in range(n)],
        "entity_id": ["QB1"] * n,
        "team_id": ["333"] * n,
        "event_date": [f"2025-09-{i + 1:02d}" for i in range(n)],
        f"avg_{target_stat}": avg_stat,
        "games_played": [i for i in range(n)],
        f"games_with_{target_stat}": games_with_stat,
        "label_stat_line": stat_lines,
        "label_started": [True] * n,
    })


def _fake_result(model_name="player-prop-passing-yards", version=1):
    return {
        "winner": {"model_name": model_name, "algorithm": "xgboost", "version": version, "rmse": 25.0},
        "promoted": True,
        "candidates": [{"algorithm": "xgboost", "rmse": 25.0}],
    }


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

    def test_excludes_a_one_off_gadget_play_despite_having_the_stat_this_game(self):
        games_with_stat = [5] * 9 + [0]
        df = _make_df(10, target_stat="passing_yards", games_with_stat=games_with_stat)

        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        assert len(filtered) == 9


class TestStatCategory:
    def test_recognizes_offensive_categories(self):
        assert train_player_prop_model._stat_category("passing_yards") == "passing"
        assert train_player_prop_model._stat_category("rushing_attempts") == "rushing"
        assert train_player_prop_model._stat_category("receiving_receptions") == "receiving"

    def test_recognizes_the_single_defensive_category(self):
        # CFBD has no separate bare "interceptions" category.
        assert train_player_prop_model._stat_category("defensive_sacks") == "defensive"
        assert train_player_prop_model.DEFENSIVE_CATEGORIES == {"defensive"}

    def test_returns_none_for_a_neutral_or_unknown_category(self):
        assert train_player_prop_model._stat_category("fumbles_recovered") is None
        assert train_player_prop_model._stat_category("kicking_field_goals_made") is None


class TestFeatureColumns:
    def test_excludes_identifiers(self):
        df = _make_df(5)

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "event_key" not in columns
        assert "team_id" not in columns

    def test_drops_columns_that_are_entirely_null(self):
        df = _make_df(5)
        df["avg_kicking_field_goals_made"] = [None] * 5

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_kicking_field_goals_made" not in columns
        assert "avg_passing_yards" in columns

    def test_excludes_the_opposing_side_of_the_ball_even_when_common(self):
        df = _make_df(100)
        df["avg_defensive_solo"] = [None] * 80 + [1.0] * 20

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_defensive_solo" not in columns

    def test_excludes_offense_for_a_defensive_target(self):
        df = _make_df(100, target_stat="defensive_sacks")
        df["avg_passing_yards"] = [None] * 80 + [250.0] * 20

        columns = train_player_prop_model._feature_columns(df, "defensive_sacks")

        assert "avg_passing_yards" not in columns

    def test_neutral_category_is_unaffected_by_the_side_rule(self):
        df = _make_df(100)
        df["avg_fumbles_recovered"] = [None] * 80 + [1.0] * 20

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_fumbles_recovered" in columns


class TestTrain:
    def test_calls_run_backtest_with_regression_task_and_every_candidate(self):
        df = _make_df(10, target_stat="passing_yards")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_player_prop_model.train(MagicMock(), df, "passing_yards")

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert result == _fake_result()

    def test_uses_the_stats_own_model_name(self):
        df = _make_df(10, target_stat="passing_yards")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        assert mock_run.call_args.args[2] == "player-prop-passing-yards"

    def test_naive_baseline_predicts_the_players_own_rolling_average(self):
        n = 10
        df = _make_df(n, target_stat="passing_yards", avg_stat=[200.0 + i for i in range(n)])

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        naive = mock_run.call_args.kwargs["naive_baseline_metrics"]
        assert naive["naive_baseline_rmse"] == pytest.approx(0.0)


class TestMain:
    def test_requires_target_stat_env_var(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.delenv("TARGET_STAT", raising=False)

        with pytest.raises(KeyError):
            train_player_prop_model.main()

    def test_loads_features_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("TARGET_STAT", "passing_yards")
        df = _make_df(10, target_stat="passing_yards")
        mock_s3 = MagicMock()

        with patch.object(train_player_prop_model, "S3Manager", return_value=mock_s3), \
             patch.object(train_player_prop_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_player_prop_model, "train", return_value=_fake_result()) as mock_train:
            train_player_prop_model.main()

        mock_load.assert_called_once_with(mock_s3, train_player_prop_model.PLAYER_FEATURES_KEY)
        mock_train.assert_called_once_with(mock_s3, df, "passing_yards")
