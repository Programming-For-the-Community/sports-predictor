from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from library.ml import backtest
from library.serving import model_loader

SPLIT = backtest.HoldoutSplit(
    pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0]}), pd.Series([1, 0, 1, 0]),
    pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [0.0] * 4}), pd.Series([1, 0, 1, 0]),
)
PERFECT = np.array([0.9, 0.1, 0.9, 0.1])
BACKWARDS = np.array([0.4, 0.6, 0.4, 0.6])


class _Adapter:
    def __init__(self, algorithm, predictions):
        self.algorithm = algorithm
        self.artifact_filename = f"model.{algorithm}"
        self._predictions = predictions

    def tune_and_fit(self, X_train, y_train):
        return f"{self.algorithm}-estimator", {}

    def predict(self, estimator, X):
        return self._predictions

    def feature_importances(self, estimator, feature_columns):
        return {}

    def serialize(self, estimator):
        return b""


def _card(**overrides):
    card = {"algorithm": "prod", "feature_columns": ["a"], "train_date_range": ["2024-09-01", "2025-11-30"], "log_loss": 0.2}
    return {**card, **overrides}


def _champion(card, loads=True, version=4):
    """Patches the production model lookup: version, card and loaded estimator."""
    load = patch.object(model_loader, "load_current_model", return_value=("est", card)) if loads else \
        patch.object(model_loader, "load_current_model", side_effect=ValueError("unpickling failed"))
    return [
        patch.object(backtest.training_common, "get_current_version", return_value=version),
        patch.object(backtest.training_common, "load_model_card", return_value=card),
        load,
    ]


def _current(card, loads=True, test_start="2025-12-01"):
    patches = _champion(card, loads)
    with patches[0], patches[1], patches[2]:
        return backtest._current_champion(MagicMock(), "nfl", "win-probability", "classification", SPLIT, "log_loss", test_start)


class TestCurrentChampion:
    def test_no_production_model_means_no_champion(self):
        with patch.object(backtest.training_common, "get_current_version", return_value=None):
            assert backtest._current_champion(MagicMock(), "nfl", "m", "classification", SPLIT, "log_loss", "2025-12-01") is None

    def test_production_is_rescored_on_this_holdout(self):
        with patch.dict(model_loader.ADAPTERS, {"prod": _Adapter("prod", BACKWARDS)}):
            champion = _current(_card())

        assert champion.basis == backtest.CHAMPION_RESCORED
        assert champion.version == 4
        assert champion.score == pytest.approx(-np.log(0.4))

    @pytest.mark.parametrize("card", [
        _card(train_date_range=["2024-09-01", "2025-12-01"]),
        _card(train_date_range=None),
        _card(feature_columns=["a", "dropped_column"]),
    ], ids=["trained-on-the-holdout", "no-training-window", "missing-column"])
    def test_falls_back_to_the_stored_score_when_rescoring_is_unfair(self, card):
        with patch.dict(model_loader.ADAPTERS, {"prod": _Adapter("prod", BACKWARDS)}):
            champion = _current(card)

        assert (champion.score, champion.basis) == (0.2, backtest.CHAMPION_STORED)

    def test_production_is_rescored_with_its_own_calibration(self):
        flat = {"method": "platt", "slope": 0.0, "intercept": 0.0}
        with patch.dict(model_loader.ADAPTERS, {"prod": _Adapter("prod", BACKWARDS)}):
            champion = _current(_card(calibration=flat))

        assert champion.score == pytest.approx(np.log(2))

    def test_falls_back_to_the_stored_score_when_the_artifact_does_not_load(self):
        champion = _current(_card(), loads=False)

        assert (champion.score, champion.basis) == (0.2, backtest.CHAMPION_STORED)


class TestPromotionDecision:
    def test_no_champion_promotes(self):
        assert backtest._promotion_decision(None, 0.5, "log_loss") == {"promoted": True, "challenger": 0.5}

    def test_records_both_scores_the_basis_and_the_margin(self):
        champion = backtest.Champion(4, 0.6, backtest.CHAMPION_RESCORED)

        decision = backtest._promotion_decision(champion, 0.595, "log_loss")

        assert decision == {
            "promoted": False, "challenger": 0.595, "champion_version": 4, "champion": 0.6,
            "champion_basis": backtest.CHAMPION_RESCORED, "required_margin": 0.01,
        }


def _run(candidates, card):
    s3 = MagicMock()
    patches = _champion(card)
    saved = []

    def save(s3, sport, model_name, algorithm, model_bytes, artifact_filename, metadata, summary_metrics):
        saved.append({"algorithm": algorithm, "version": 10 + len(saved), **metadata})
        return saved[-1]

    with patches[0], patches[1], patches[2], \
         patch.dict(model_loader.ADAPTERS, {"prod": _Adapter("prod", PERFECT)}), \
         patch.object(backtest.training_common, "load_run_progress", return_value=None), \
         patch.object(backtest.training_common, "save_run_progress"), \
         patch.object(backtest.training_common, "clear_run_progress"), \
         patch.object(backtest.training_common, "save_model_artifact", side_effect=save), \
         patch.object(backtest.training_common, "set_current_version") as set_current:
        result = backtest.run_backtest(
            s3, "nfl", "win-probability", "classification", SPLIT, candidates, {},
            {"train_rows": 4, "test_rows": 4, "test_date_range": ["2025-12-01", "2026-01-31"]},
            ["accuracy", "log_loss"], "log_loss", "run-1",
        )
    return result, saved, set_current


class TestRunBacktestAgainstTheChampion:
    def test_a_candidate_that_only_beats_the_stored_score_is_held_back_and_never_saved(self):
        # Stored log-loss 0.2 is beaten by the candidate, but production re-scores to the same perfect predictions.
        result, saved, set_current = _run([_Adapter("xgboost", PERFECT)], _card())

        assert result["promotions"] == []
        assert saved == []
        set_current.assert_not_called()

    def test_a_winner_carries_the_decision_and_becomes_the_bar_for_later_candidates(self):
        weak_card = _card(algorithm="prod_weak")
        with patch.dict(model_loader.ADAPTERS, {"prod_weak": _Adapter("prod_weak", BACKWARDS)}):
            result, saved, set_current = _run([_Adapter("xgboost", PERFECT), _Adapter("lightgbm", PERFECT)], weak_card)

        assert [c["algorithm"] for c in result["promotions"]] == ["xgboost"]
        decision = saved[0]["promotion_decision"]
        assert decision["promoted"]
        assert decision["champion_basis"] == backtest.CHAMPION_RESCORED
        set_current.assert_called_once()
        assert set_current.call_args.args[1:] == ("nfl", "win-probability", 10)


def _stage_split(X_test, y_test):
    return backtest.HoldoutSplit(X_test, y_test, X_test, y_test)


class TestHoldoutBySeasonStage:
    def test_player_rows_split_on_their_own_games_this_season(self):
        X = pd.DataFrame({"games_this_season": [0, 2, 5, 5]})
        y = pd.Series([3.0, 5.0, 10.0, 10.0])

        result = backtest._holdout_by_season_stage("regression", np.array([4.0, 4.0, 10.0, 10.0]), _stage_split(X, y))

        assert result["early_season"]["rows"] == 2
        assert result["early_season"]["mae"] == pytest.approx(1.0)
        assert result["rest_of_season"] == {"rows": 2, "rmse": 0.0, "mae": 0.0}

    def test_event_rows_use_the_side_with_fewer_games_this_season(self):
        X = pd.DataFrame({"home_games_this_season": [5, 5], "away_games_this_season": [1, 5]})

        result = backtest._holdout_by_season_stage("regression", np.array([1.0, 1.0]), _stage_split(X, pd.Series([1.0, 1.0])))

        assert (result["early_season"]["rows"], result["rest_of_season"]["rows"]) == (1, 1)

    def test_a_single_outcome_stage_still_scores_log_loss(self):
        X = pd.DataFrame({"games_this_season": [0, 0, 5, 5]})

        result = backtest._holdout_by_season_stage("classification", PERFECT, _stage_split(X, pd.Series([1, 1, 1, 0])))

        assert result["early_season"]["rows"] == 2
        assert result["early_season"]["log_loss"] > 0

    def test_rows_without_the_column_have_no_breakdown(self):
        assert backtest._holdout_by_season_stage("regression", np.array([1.0]), _stage_split(pd.DataFrame({"a": [1]}), pd.Series([1.0]))) is None

    def test_rows_with_no_count_fall_in_neither_stage(self):
        X = pd.DataFrame({"games_this_season": [None, 5]})

        result = backtest._holdout_by_season_stage("regression", np.array([1.0, 1.0]), _stage_split(X, pd.Series([1.0, 1.0])))

        assert result == {"rest_of_season": {"rows": 1, "rmse": 0.0, "mae": 0.0}}
