"""
Unit tests for the NHL player-prop training entrypoint: skater stats and
goalie stats read different datasets through the same shared trainer.

library.ml.backtest.run_backtest is mocked -- these tests verify the
script's own wiring, not the tournament or any real algorithm fitting.
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import train_player_prop_model


def _skater_df(n=20):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "player_key": ["SPORT#NHL#PLAYER#s1"] * n,
        "entity_id": ["s1"] * n,
        "team_id": ["13"] * n,
        "opponent_id": ["1"] * n,
        "event_date": [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)],
        "avg_shots_total": [3.0] * n,
        "games_with_shots_total": [10] * n,
        "avg_goals": [0.3] * n,
        "games_with_goals": [10] * n,
        "shots_per_60": [9.0] * n,
        "is_home": [i % 2 for i in range(n)],
        "mostly_empty": [1.0] + [None] * (n - 1),
        "label_stat_line": [json.dumps({"shots_total": 2 + i % 3, "goals": i % 2}) for i in range(n)],
        "label_started": [None] * n,
    })


def _goalie_df(n=20):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "player_key": ["SPORT#NHL#PLAYER#g1"] * n,
        "entity_id": ["g1"] * n,
        "team_id": ["13"] * n,
        "opponent_id": ["1"] * n,
        "event_date": [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)],
        "avg_saves": [27.0] * n,
        "games_with_saves": [10] * n,
        "save_pct_career": [0.91] * n,
        "opponent_shots_for_last10": [31.0] * n,
        "label_stat_line": [json.dumps({"saves": 28, "goals_against": 2})] * n,
        "label_started": [True] * n,
    })


def _fake_result():
    return {"promotions": [{"version": 1}], "candidates": [{"algorithm": "xgboost"}]}


class TestDatasetRouting:
    @pytest.mark.parametrize("stat", ["shots_total", "points", "goals", "assists", "hits", "blocked_shots"])
    def test_skater_stats_use_the_player_dataset(self, stat):
        assert train_player_prop_model._job_for(stat) is train_player_prop_model._skater_job

    @pytest.mark.parametrize("stat", ["saves", "goals_against"])
    def test_goalie_stats_use_the_goalie_dataset(self, stat):
        assert train_player_prop_model._job_for(stat) is train_player_prop_model._goalie_job

    def test_unknown_stat_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown TARGET_STAT"):
            train_player_prop_model._job_for("rebounds")

    def _main(self, monkeypatch, stat, df):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("TARGET_STAT", stat)
        mock_s3 = MagicMock()
        with patch.object(train_player_prop_model.training_common, "S3Manager", return_value=mock_s3), \
             patch.object(train_player_prop_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_player_prop_model, "train", return_value=_fake_result()) as mock_train:
            train_player_prop_model.main()
        mock_train.assert_called_once_with(mock_s3, df, stat)
        return mock_load.call_args.args[1]

    def test_main_loads_the_dataset_that_matches_the_target(self, monkeypatch):
        assert self._main(monkeypatch, "goals", _skater_df()) == "nhl/training-data/player_features.parquet"
        assert self._main(monkeypatch, "saves", _goalie_df()) == "nhl/training-data/goalie_features.parquet"


class TestTrain:
    def _run(self, df, stat):
        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, stat)
        return mock_run.call_args

    def test_skater_prop_is_a_regression_on_the_stat_from_his_stat_line(self):
        call = self._run(_skater_df(), "shots_total")

        assert call.args[1:3] == ("nhl", "player-prop-shots-total")
        assert call.kwargs["task"] == "regression"
        assert call.kwargs["promotion_metric"] == "rmse"
        assert call.kwargs["split"].y_train.tolist()[:3] == [2.0, 3.0, 4.0]
        assert call.kwargs["extra_metadata"]["target_stat"] == "shots_total"

    def test_features_exclude_identifiers_labels_and_nearly_empty_columns(self):
        call = self._run(_skater_df(40), "shots_total")

        assert list(call.kwargs["split"].X_train.columns) == [
            "avg_shots_total", "games_with_shots_total", "avg_goals", "games_with_goals", "shots_per_60", "is_home",
        ]

    def test_naive_baseline_is_the_players_own_rolling_average(self):
        call = self._run(_goalie_df(), "saves")

        # Every test row averaged 27 saves and made 28.
        assert call.kwargs["naive_baseline_metrics"] == {"naive_baseline_rmse": 1.0, "naive_baseline_mae": 1.0}
        assert call.args[2] == "player-prop-saves"
        assert "save_pct_career" in call.kwargs["split"].X_train.columns

    def test_players_without_an_established_history_of_the_stat_are_dropped(self):
        df = _skater_df()
        df.loc[:9, "games_with_shots_total"] = 1

        call = self._run(df, "shots_total")

        assert len(call.kwargs["split"].X_train) + len(call.kwargs["split"].X_test) == 10
