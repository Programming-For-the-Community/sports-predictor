"""
Unit tests for the NHL win-probability and score training entrypoints.

library.ml.backtest.run_backtest is mocked -- these tests verify each
script's own wiring (feature columns, labels, baselines), not the
tournament itself or any real algorithm fitting.
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import nhl_training
import train_score_model
import train_win_probability_model


def _make_df(n=10):
    return pd.DataFrame({
        "event_key": [f"E{i}" for i in range(n)],
        "event_date": [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)],
        "season": [2026] * n,
        "home_entity_id": ["13"] * n,
        "away_entity_id": ["1"] * n,
        "home_elo": [1500.0 + i for i in range(n)],
        "away_elo": [1500.0] * n,
        "elo_diff": [float(i) for i in range(n)],
        "home_goals_for_last10": [3.0] * n,
        "home_goals_against_last10": [2.5] * n,
        "away_goals_for_last10": [2.0] * n,
        "away_goals_against_last10": [3.5] * n,
        "home_games_this_season": [20] * n,
        "away_games_this_season": [20] * n,
        "label_home_won": [i % 2 == 0 for i in range(n)],
        "label_home_score": [4, 2] * (n // 2),
        "label_away_score": [3, 3] * (n // 2),
        "label_home_goals": [3, 2] * (n // 2),
        "label_away_goals": [3, 3] * (n // 2),
        "label_went_to_overtime": [True, False] * (n // 2),
        "label_decided_by_shootout": [True, False] * (n // 2),
    })


FEATURES = [
    "home_elo", "away_elo", "elo_diff", "home_goals_for_last10", "home_goals_against_last10",
    "away_goals_for_last10", "away_goals_against_last10", "home_games_this_season", "away_games_this_season",
]


def _fake_result():
    return {"promotions": [{"version": 1}], "candidates": [{"algorithm": "xgboost"}]}


class TestSharedConfig:
    def test_season_is_never_a_feature(self):
        assert "season" in nhl_training.EXTRA_NON_FEATURE_COLUMNS

    def test_the_lineup_group_serving_cannot_rebuild_is_excluded(self):
        assert nhl_training.EXCLUDED_FEATURE_GROUPS == frozenset({"lineup"})
        assert {"home_ice_time_share_missing", "away_top_scorers_out", "home_team_injury_count"} <= nhl_training.EXTRA_NON_FEATURE_COLUMNS
        assert "home_shot_share_last25" not in nhl_training.EXTRA_NON_FEATURE_COLUMNS
        assert "home_goalie_save_pct_career" not in nhl_training.EXTRA_NON_FEATURE_COLUMNS

    def test_both_scripts_read_the_feature_engineering_jobs_dataset(self):
        assert train_win_probability_model.EVENT_FEATURES_KEY == "nhl/training-data/event_features.parquet"
        assert train_score_model.EVENT_FEATURES_KEY == "nhl/training-data/event_features.parquet"


class TestWinProbability:
    def test_feature_columns_exclude_identifiers_season_and_every_label(self):
        assert train_win_probability_model._feature_columns(_make_df()) == FEATURES

    def test_runs_the_classification_tournament_on_the_home_won_label(self):
        with patch.object(train_win_probability_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_win_probability_model.train(MagicMock(), _make_df())

        call = mock_run.call_args
        assert call.args[1:3] == ("nhl", "win-probability")
        assert call.kwargs["task"] == "classification"
        assert call.kwargs["promotion_metric"] == "log_loss"
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostClassifierAdapter", "LogisticRegressionAdapter",
            "RandomForestClassifierAdapter", "MLPClassifierAdapter", "LightGBMClassifierAdapter",
        }
        assert list(call.kwargs["split"].X_train.columns) == FEATURES
        assert (len(call.kwargs["split"].X_train), len(call.kwargs["split"].X_test)) == (8, 2)
        assert call.kwargs["split"].y_train.name == "label_home_won"
        assert call.kwargs["naive_baseline_metrics"] == {"naive_baseline_accuracy": 0.5}
        assert result == _fake_result()

    def test_main_loads_the_dataset_and_delegates_to_train(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        df, mock_s3 = _make_df(), MagicMock()

        with patch.object(train_win_probability_model.training_common, "S3Manager", return_value=mock_s3), \
             patch.object(train_win_probability_model.training_common, "load_features", return_value=df) as mock_load, \
             patch.object(train_win_probability_model, "train", return_value=_fake_result()) as mock_train:
            train_win_probability_model.main()

        mock_load.assert_called_once_with(mock_s3, "nhl/training-data/event_features.parquet")
        mock_train.assert_called_once_with(mock_s3, df)


class TestScore:
    def _run(self, score_target, rows=10):
        with patch.object(train_score_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_score_model.train(MagicMock(), _make_df(rows), score_target)
        return mock_run.call_args

    def test_margin_label_is_the_final_score_difference_shootout_goal_included(self):
        call = self._run("margin")

        assert call.args[1:3] == ("nhl", "score-margin")
        assert call.kwargs["task"] == "regression"
        assert call.kwargs["promotion_metric"] == "rmse"
        assert call.kwargs["split"].y_train.tolist() == [1, -1] * 4
        assert call.kwargs["extra_metadata"]["score_target"] == "margin"

    def test_home_and_away_targets(self):
        home, away = self._run("home_score"), self._run("away_score")

        assert (home.args[2], away.args[2]) == ("home-score", "away-score")
        assert home.kwargs["split"].y_train.tolist() == [4, 2] * 4
        assert away.kwargs["split"].y_train.tolist() == [3, 3] * 4

    def test_baseline_alias_columns_are_added_but_never_become_features(self):
        call = self._run("home_score")

        assert list(call.kwargs["split"].X_train.columns) == FEATURES
        assert train_score_model.NAIVE_BASELINE_SOURCES == {
            "home_avg_points_scored": "home_goals_for_last10", "home_avg_points_allowed": "home_goals_against_last10",
            "away_avg_points_scored": "away_goals_for_last10", "away_avg_points_allowed": "away_goals_against_last10",
        }

    def test_naive_baseline_averages_a_teams_scoring_with_its_opponents_allowing(self):
        call = self._run("home_score")

        # Baseline predicts (3.0 + 3.5) / 2 = 3.25 for the home side; the two test rows scored 4 and 2.
        assert call.kwargs["naive_baseline_metrics"]["naive_baseline_mae"] == pytest.approx((0.75 + 1.25) / 2)

    def test_margin_learns_a_correction_to_the_elo_baseline(self):
        # The baseline needs at least 30 rated training rows to fit.
        assert self._run("margin", rows=50).kwargs["options"].target_baseline["kind"] == "elo_linear"
        assert self._run("home_score", rows=50).kwargs["options"].target_baseline is None

    def test_unknown_target_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown SCORE_TARGET"):
            train_score_model.train(MagicMock(), _make_df(), "total")

    def test_main_reads_the_target_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("SCORE_TARGET", "margin")
        df, mock_s3 = _make_df(), MagicMock()

        with patch.object(train_score_model.training_common, "S3Manager", return_value=mock_s3), \
             patch.object(train_score_model.training_common, "load_features", return_value=df), \
             patch.object(train_score_model, "train", return_value=_fake_result()) as mock_train:
            train_score_model.main()

        mock_train.assert_called_once_with(mock_s3, df, "margin")


class TestImageContents:
    def test_every_script_a_task_definition_runs_is_copied_into_the_image(self):
        import glob
        import os
        import re

        root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        with open(os.path.join(root, "Source", "model-training", "nhl", "Dockerfile"), encoding="utf-8") as dockerfile:
            image = dockerfile.read()
        copied = set(re.findall(r"model-training/nhl/(\w+\.py)", image))
        commands = set(re.findall(r'CMD \["(\w+\.py)"\]', image))
        for path in glob.glob(os.path.join(root, "Terraform", "ecs-task-nhl-train-*.tf")):
            with open(path, encoding="utf-8") as task:
                commands.update(re.findall(r'command\s*=\s*\["(\w+\.py)"\]', task.read()))

        assert commands == {"train_win_probability_model.py", "train_score_model.py", "train_player_prop_model.py"}
        assert commands | {"nhl_training.py"} <= copied
