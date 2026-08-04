"""
Unit tests for the NFL player-prop training entrypoint.

library.ml.backtest.run_backtest is mocked here -- these tests verify
train_player_prop_model.py's own orchestration (target-stat filtering,
column selection, naive baseline computation, what gets handed to
run_backtest), not the tournament itself (see Source/tests/library/ml/
test_backtest.py) or any real algorithm fitting (see Source/tests/
library/ml/test_model_types.py).
"""
import io
import json
from unittest.mock import MagicMock, patch

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

    def test_label_column_never_leaks_into_feature_columns(self):
        df = _make_df(5, target_stat="passing_yards")
        filtered = train_player_prop_model._filter_to_target_stat(df, "passing_yards")

        columns = train_player_prop_model._feature_columns(filtered, "passing_yards")

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

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

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

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_field_goals_made" not in columns
        assert "avg_passing_yards" in columns

    def test_drops_a_column_below_the_non_null_fraction(self):
        # 1 real value out of 100 rows is 1% -- below MIN_NON_NULL_FRACTION
        # (5%). Needs a large n; a tiny fixture would let even one value
        # trivially clear 5% of the row count. avg_receiving_yards, not a
        # defensive stat, so this stays isolated from the separate
        # opposing-side-category exclusion (see TestOpposingSideExclusion).
        df = _make_df(100)
        df["avg_receiving_yards"] = [None] * 99 + [1.0]

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_receiving_yards" not in columns

    def test_keeps_a_column_at_or_above_the_non_null_fraction(self):
        df = _make_df(100)
        df["avg_rushing_yards"] = [None] * 90 + [10.0] * 10  # exactly 10%

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_rushing_yards" in columns

    def test_excludes_the_opposing_side_of_the_ball_even_when_common(self):
        # avg_defensive_solo_tackles at 20% non-null -- comfortably clears
        # MIN_NON_NULL_FRACTION (real signal, e.g. a QB tackling after an
        # interception -- see the player-prop-passing-yards v3/v4 model
        # cards), but must still be excluded from an offensive target by
        # the opposing-side-category rule, not just the null-fraction one.
        df = _make_df(100)
        df["avg_defensive_solo_tackles"] = [None] * 80 + [1.0] * 20

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_defensive_solo_tackles" not in columns

    def test_keeps_the_same_side_of_the_ball(self):
        df = _make_df(100)
        df["avg_rushing_touchdowns"] = [None] * 80 + [1.0] * 20

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_rushing_touchdowns" in columns

    def test_excludes_offense_for_a_defensive_target(self):
        df = _make_df(100, target_stat="defensive_sacks")
        df["avg_passing_yards"] = [None] * 80 + [250.0] * 20

        columns = train_player_prop_model._feature_columns(df, "defensive_sacks")

        assert "avg_passing_yards" not in columns

    def test_neutral_category_is_unaffected_by_the_side_rule(self):
        # fumbles is deliberately uncategorized -- real for both ball
        # carriers and defenders forcing/recovering them -- so it should
        # survive purely on MIN_NON_NULL_FRACTION, not get excluded
        # outright the way a defensive stat would for an offensive target.
        df = _make_df(100)
        df["avg_fumbles_recovered"] = [None] * 80 + [1.0] * 20

        columns = train_player_prop_model._feature_columns(df, "passing_yards")

        assert "avg_fumbles_recovered" in columns


class TestStatCategory:
    def test_recognizes_offensive_categories(self):
        assert train_player_prop_model._stat_category("passing_yards") == "passing"
        assert train_player_prop_model._stat_category("rushing_touchdowns") == "rushing"
        assert train_player_prop_model._stat_category("receiving_targets") == "receiving"

    def test_recognizes_defensive_categories(self):
        assert train_player_prop_model._stat_category("defensive_sacks") == "defensive"
        assert train_player_prop_model._stat_category("interceptions") == "interceptions"

    def test_returns_none_for_a_neutral_or_unknown_category(self):
        assert train_player_prop_model._stat_category("fumbles_recovered") is None
        assert train_player_prop_model._stat_category("punting_punts") is None


class TestOpposingSideCategories:
    def test_offensive_target_opposes_defensive_categories(self):
        opposing = train_player_prop_model._opposing_side_categories("passing_yards")

        assert opposing == train_player_prop_model.DEFENSIVE_CATEGORIES

    def test_defensive_target_opposes_offensive_categories(self):
        opposing = train_player_prop_model._opposing_side_categories("defensive_sacks")

        assert opposing == train_player_prop_model.OFFENSIVE_CATEGORIES

    def test_neutral_target_opposes_nothing(self):
        assert train_player_prop_model._opposing_side_categories("fumbles_recovered") == set()


class TestTrain:
    def test_calls_run_backtest_with_regression_task_and_every_candidate(self):
        df = _make_df(10, target_stat="passing_yards")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            result = train_player_prop_model.train(MagicMock(), df, "passing_yards")

        call = mock_run.call_args
        assert call.kwargs["task"] == "regression"
        assert call.kwargs["candidates"] == train_player_prop_model.CANDIDATES
        assert {type(c).__name__ for c in call.kwargs["candidates"]} == {
            "XGBoostRegressorAdapter", "ElasticNetAdapter", "RandomForestRegressorAdapter", "MLPRegressorAdapter",
        }
        assert result == _fake_result()

    def test_uses_the_stats_own_model_name(self):
        df = _make_df(10, target_stat="passing_yards")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        assert mock_run.call_args.args[2] == "player-prop-passing-yards"

    def test_filters_to_target_stat_before_splitting(self):
        # 3 of 10 rows record a different stat -- the 80/20 split must
        # apply to the remaining 7, not the original 10.
        df = _make_df(10, target_stat="passing_yards", missing_stat_rows=3)

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        extra = mock_run.call_args.kwargs["extra_metadata"]
        assert extra["train_rows"] + extra["test_rows"] == 7

    def test_includes_target_stat_in_extra_metadata(self):
        df = _make_df(10, target_stat="passing_yards")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        assert mock_run.call_args.kwargs["extra_metadata"]["target_stat"] == "passing_yards"

    def test_naive_baseline_predicts_the_players_own_rolling_average(self):
        # Set avg_passing_yards to exactly match each row's own label
        # (200 + i, from _make_df's stat_line values) -- the naive
        # baseline's error should be exactly zero.
        n = 10
        df = _make_df(n, target_stat="passing_yards", avg_stat=[200.0 + i for i in range(n)])

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        naive = mock_run.call_args.kwargs["naive_baseline_metrics"]
        assert naive["naive_baseline_rmse"] == pytest.approx(0.0)
        assert naive["naive_baseline_mae"] == pytest.approx(0.0)

    def test_promotion_metric_is_rmse(self):
        df = _make_df(10, target_stat="passing_yards")

        with patch.object(train_player_prop_model.backtest, "run_backtest", return_value=_fake_result()) as mock_run:
            train_player_prop_model.train(MagicMock(), df, "passing_yards")

        assert mock_run.call_args.kwargs["promotion_metric"] == "rmse"


class TestMain:
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
