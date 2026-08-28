"""
Unit tests for aws-lambdas/pga/predict/season_projection.py -- the
compute-Lambda side of the FedEx Cup season simulation. live_features/
model_loader/season_simulation are exercised for real where cheap
(season_simulation especially -- it's pure and fast); storage/s3/
predictions_table are MagicMocks.
"""
from unittest.mock import MagicMock, patch

import season_projection


def _field_event(event_id, event_date, season, status="completed", tournament_name=None, is_major=False, participants=None, course_id="65"):
    return {
        "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_type": "field",
        "event_date": event_date, "season": season, "status": status,
        "tournament_name": tournament_name, "is_major": is_major, "course_id": course_id,
        "participants": participants or [],
    }


def _participant(entity_id, finish_position=None):
    return {"entity_id": entity_id, "result": {"finish_position": finish_position, "status": "finished"}}


def _by_status(events):
    """storage.get_all_events side_effect that actually discriminates by
    the status kwarg, the way the real DynamoDB-backed FeatureStorage does
    -- same precedent as tests/aws-lambdas/nba/test_predict_season_
    simulation.py's own mocks. A single return_value=events mock (the
    prior shape here) would silently mask a caller that forgets to pass
    status at all, since it'd return the same full list regardless -- that
    exact gap was real production behavior once (get_all_events(SPORT)
    defaults to status="completed", so a status-blind call site never saw
    a scheduled event at all)."""
    def _get_all_events(sport, status="completed"):
        return [e for e in events if e.get("status") == status]
    return _get_all_events


class TestSeasonStandingsInputs:
    def test_no_field_event_ever_stored_returns_a_null_season(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([])
        result = season_projection._season_standings_inputs(storage)
        assert result["current_season"] is None
        assert result["tracked_roster"] == []

    def test_current_season_is_the_latest_seasons_own_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2025-06-01", season=2025),
            _field_event("2", "2026-06-01", season=2026, status="scheduled"),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert result["current_season"] == 2026

    def test_tracked_roster_is_every_golfer_with_a_real_completed_start_this_season(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2026-06-01", season=2026, participants=[_participant("a"), _participant("b")]),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert result["tracked_roster"] == ["a", "b"]

    def test_current_points_accumulate_across_multiple_completed_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2026-01-01", season=2026, tournament_name="Event A", participants=[_participant("a", 1)]),
            _field_event("2", "2026-02-01", season=2026, tournament_name="Event B", participants=[_participant("a", 2)]),
        ])
        result = season_projection._season_standings_inputs(storage)
        # 1st (500) + 2nd (300) at regular-tier -- both events unlisted, fail open to "regular".
        assert result["current_points"]["a"] == 800.0

    def test_prior_season_events_are_scoped_to_exactly_season_minus_1(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2024-06-01", season=2024),
            _field_event("2", "2025-06-01", season=2025),
            _field_event("3", "2026-06-01", season=2026, participants=[_participant("a")]),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert [e["event_id"] for e in result["prior_season_events"]] == ["2"]

    def test_remaining_events_are_this_seasons_own_not_yet_completed_ones_sorted_chronologically(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("2", "2026-09-01", season=2026, status="scheduled"),
            _field_event("1", "2026-08-01", season=2026, status="scheduled"),
            _field_event("3", "2025-08-01", season=2025, status="scheduled"),  # wrong season
        ])
        result = season_projection._season_standings_inputs(storage)
        assert [e["event_id"] for e in result["remaining_events"]] == ["1", "2"]

    def test_a_status_blind_call_site_would_never_see_a_scheduled_event_again(self):
        # get_all_events(SPORT) with no status kwarg defaults to
        # status="completed", which would silently empty remaining_events
        # forever.
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2026-08-20", season=2026, tournament_name="BMW Championship", participants=[_participant("a", 1)]),
            _field_event("2", "2026-08-27", season=2026, status="scheduled", tournament_name="Tour Championship"),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert [e["event_id"] for e in result["remaining_events"]] == ["2"]


class TestSplitRemainingEvents:
    def test_identifies_tour_championship_by_its_own_real_name(self):
        events = [
            _field_event("1", "2026-08-20", season=2026, status="scheduled", tournament_name="BMW Championship"),
            _field_event("2", "2026-08-27", season=2026, status="scheduled", tournament_name="Tour Championship"),
        ]
        split = season_projection._split_remaining_events(events, 2026)
        assert split["tour_championship"]["event_id"] == "2"

    def test_falls_back_to_the_chronologically_last_event_when_the_name_is_missing(self):
        events = [
            _field_event("1", "2026-08-20", season=2026, status="scheduled", tournament_name=None),
            _field_event("2", "2026-08-27", season=2026, status="scheduled", tournament_name=None),
        ]
        split = season_projection._split_remaining_events(events, 2026)
        assert split["tour_championship"]["event_id"] == "2"

    def test_fedex_st_jude_and_bmw_are_identified_by_the_real_points_table_override(self):
        events = [
            _field_event("1", "2026-08-06", season=2026, status="scheduled", tournament_name="FedEx St. Jude Championship"),
            _field_event("2", "2026-08-13", season=2026, status="scheduled", tournament_name="BMW Championship"),
            _field_event("3", "2026-08-27", season=2026, status="scheduled", tournament_name="Tour Championship"),
        ]
        split = season_projection._split_remaining_events(events, 2026)
        assert split["fedex_st_jude"]["event_id"] == "1"
        assert split["bmw_championship"]["event_id"] == "2"

    def test_before_playoffs_excludes_all_3_special_events(self):
        events = [
            _field_event("1", "2026-07-01", season=2026, status="scheduled", tournament_name="Regular Event"),
            _field_event("2", "2026-08-06", season=2026, status="scheduled", tournament_name="FedEx St. Jude Championship"),
            _field_event("3", "2026-08-13", season=2026, status="scheduled", tournament_name="BMW Championship"),
            _field_event("4", "2026-08-27", season=2026, status="scheduled", tournament_name="Tour Championship"),
        ]
        split = season_projection._split_remaining_events(events, 2026)
        assert [e["event_id"] for e in split["before_playoffs"]] == ["1"]

    def test_no_remaining_events_at_all_gives_every_slot_none(self):
        split = season_projection._split_remaining_events([], 2026)
        assert split == {"before_playoffs": [], "fedex_st_jude": None, "bmw_championship": None, "tour_championship": None}


class TestBatchScoreGolfers:
    def test_scores_every_golfer_in_the_given_rows(self):
        model_card = {"feature_columns": ["f1", "f2"], "algorithm": "xgboost_regressor"}
        golfer_rows = {"a": {"f1": 1.0, "f2": 2.0}, "b": {"f1": 3.0, "f2": 4.0}}
        fake_adapter = MagicMock()
        fake_adapter.predict.return_value = [0.5, 0.7]
        with patch.dict(season_projection.ADAPTERS, {"xgboost_regressor": fake_adapter}):
            result = season_projection._batch_score_golfers(MagicMock(), model_card, golfer_rows)
        assert result == {"a": 0.5, "b": 0.7}

    def test_missing_or_non_numeric_feature_values_become_nan_not_a_crash(self):
        model_card = {"feature_columns": ["f1"], "algorithm": "xgboost_regressor"}
        golfer_rows = {"a": {"f1": None}}
        fake_adapter = MagicMock()

        def _predict(estimator, X):
            import math
            assert math.isnan(X.iloc[0]["f1"])
            return [0.1]

        fake_adapter.predict.side_effect = _predict
        with patch.dict(season_projection.ADAPTERS, {"xgboost_regressor": fake_adapter}):
            result = season_projection._batch_score_golfers(MagicMock(), model_card, golfer_rows)
        assert result == {"a": 0.1}


class TestBuildSeasonProjection:
    def _model(self, feature_columns=("rounds_completed_this_week",)):
        return MagicMock(), {"feature_columns": list(feature_columns), "algorithm": "xgboost_regressor", "rmse": 3.0}

    def test_no_field_event_ever_stored_returns_none(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([])
        assert season_projection.build_season_projection(storage, MagicMock(), MagicMock()) is None

    def test_only_tour_championship_remaining_still_produces_a_full_projection(self):
        # BMW Championship already completed, only TOUR Championship left.
        storage = MagicMock()
        estimator, model_card = self._model()
        bmw_completed = _field_event(
            "1", "2026-08-20", season=2026, tournament_name="BMW Championship",
            participants=[_participant("a", 1), _participant("b", 2)],
        )
        tour_championship = _field_event(
            "2", "2026-08-27", season=2026, status="scheduled", tournament_name="Tour Championship",
        )
        storage.get_all_events.side_effect = _by_status([bmw_completed, tour_championship])
        storage.get_entity.return_value = {"name": "Golfer", "metadata": {"country": "USA"}}
        storage.get_team_events.return_value = []
        fake_adapter = MagicMock()
        fake_adapter.predict.return_value = [-2.0, -1.0]

        with patch.object(season_projection.model_loader, "load_current_model", return_value=(estimator, model_card)), \
             patch.dict(season_projection.ADAPTERS, {"xgboost_regressor": fake_adapter}):
            result = season_projection.build_season_projection(storage, MagicMock(), MagicMock())

        assert result is not None
        assert result["season"] == 2026
        assert {row["entity_id"] for row in result["standings"]} == {"a", "b"}
        # BMW Championship's real 4x-regular-tier points already earned.
        a_row = next(r for r in result["standings"] if r["entity_id"] == "a")
        assert a_row["current_points"] == 2000.0  # 1st at bmw_championship tier = 500 * 4

    def test_nothing_remaining_at_all_uses_real_final_standings_not_a_simulation(self):
        storage = MagicMock()
        tour_championship = _field_event(
            "1", "2026-08-27", season=2026, tournament_name="Tour Championship",
            participants=[_participant("a", 1), _participant("b", 2)],
        )
        storage.get_all_events.side_effect = _by_status([tour_championship])
        storage.get_entity.return_value = None

        result = season_projection.build_season_projection(storage, MagicMock(), MagicMock())

        assert result["simulations"] == 0
        champion_row = next(r for r in result["standings"] if r["entity_id"] == "a")
        assert champion_row["champion_probability"] == 1.0
        runner_up_row = next(r for r in result["standings"] if r["entity_id"] == "b")
        assert runner_up_row["champion_probability"] == 0.0


class TestRunScheduled:
    def test_writes_to_s3_when_a_projection_is_produced(self):
        s3 = MagicMock()
        with patch.object(season_projection, "build_season_projection", return_value={"season": 2026, "standings": []}):
            season_projection.run_scheduled(MagicMock(), s3, MagicMock())
        s3.put_json.assert_called_once()

    def test_skips_the_s3_write_when_there_is_no_season_to_project(self):
        s3 = MagicMock()
        with patch.object(season_projection, "build_season_projection", return_value=None):
            result = season_projection.run_scheduled(MagicMock(), s3, MagicMock())
        s3.put_json.assert_not_called()
        assert result == {"sport": "pga", "skipped": True}
