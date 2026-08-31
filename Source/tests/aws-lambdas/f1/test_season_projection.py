"""
Unit tests for f1/predict/season_projection.py. live_features/model_loader/
season_simulation are exercised for real where cheap (the pure standings/
points math especially); storage/s3/predictions_table are MagicMocks.
"""
from unittest.mock import MagicMock, patch

import season_projection


def _field_event(event_id, event_date, season, status="completed", participants=None, event_type="field", circuit_id="bahrain"):
    return {
        "event_key": f"SPORT#F1#EVENT#{event_id}", "event_id": event_id, "event_type": event_type,
        "event_date": event_date, "season": season, "status": status, "circuit_id": circuit_id,
        "participants": participants or [],
    }


def _participant(entity_id, finish_position=None, constructor_entity_id="red_bull", fastest_lap=False):
    return {
        "entity_id": entity_id, "constructor_entity_id": constructor_entity_id,
        "result": {"finish_position": finish_position, "status": "finished" if finish_position else "dnf", "fastest_lap": fastest_lap},
    }


def _by_status(events):
    def _get_all_events(sport, status="completed"):
        return [e for e in events if e.get("status") == status]
    return _get_all_events


class TestCurrentDriverPoints:
    def test_accumulates_across_multiple_completed_races(self):
        events = [
            _field_event("1", "2024-03-02", 2024, participants=[_participant("a", 1)]),
            _field_event("2", "2024-03-09", 2024, participants=[_participant("a", 2)]),
        ]
        points = season_projection._current_driver_points(events)
        assert points["a"] == 25.0 + 18.0

    def test_fastest_lap_bonus_applied_to_a_top_10_finisher(self):
        events = [_field_event("1", "2024-03-02", 2024, participants=[_participant("a", 1, fastest_lap=True)])]
        points = season_projection._current_driver_points(events)
        assert points["a"] == 26.0

    def test_sprint_races_use_the_sprint_points_table_no_fastest_lap_bonus(self):
        events = [_field_event("1", "2024-03-02", 2024, participants=[_participant("a", 1, fastest_lap=True)], event_type="sprint")]
        points = season_projection._current_driver_points(events)
        assert points["a"] == 8.0  # sprint win, no +1 (bonus is field-only)

    def test_a_dnf_still_appears_in_the_dict_at_zero(self):
        events = [_field_event("1", "2024-03-02", 2024, participants=[_participant("a", None)])]
        points = season_projection._current_driver_points(events)
        assert points["a"] == 0.0


class TestSeasonStandingsInputs:
    def test_no_event_ever_stored_returns_a_null_season(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([])
        result = season_projection._season_standings_inputs(storage)
        assert result["current_season"] is None
        assert result["tracked_roster"] == []

    def test_current_season_is_the_latest_seasons_own_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2025-06-01", 2025),
            _field_event("2", "2026-06-01", 2026, status="scheduled"),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert result["current_season"] == 2026

    def test_tracked_roster_is_the_most_recent_field_races_own_lineup(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2024-03-02", 2024, participants=[_participant("a"), _participant("b")]),
            _field_event("2", "2024-03-09", 2024, participants=[_participant("a"), _participant("c")]),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert set(result["tracked_roster"]) == {"a", "c"}

    def test_driver_to_constructor_comes_from_the_same_most_recent_race(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2024-03-09", 2024, participants=[_participant("a", constructor_entity_id="mercedes")]),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert result["driver_to_constructor"]["a"] == "mercedes"

    def test_remaining_events_are_this_seasons_own_scheduled_field_races_sorted(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("2", "2026-09-01", 2026, status="scheduled"),
            _field_event("1", "2026-08-01", 2026, status="scheduled"),
            _field_event("3", "2025-08-01", 2025, status="scheduled"),  # wrong season
        ])
        result = season_projection._season_standings_inputs(storage)
        assert [e["event_id"] for e in result["remaining_events"]] == ["1", "2"]

    def test_a_replaced_drivers_banked_points_still_count_but_they_leave_the_roster(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2024-03-02", 2024, participants=[_participant("old_driver", 1)]),
            _field_event("2", "2024-03-09", 2024, participants=[_participant("new_driver", 5)]),
        ])
        result = season_projection._season_standings_inputs(storage)
        assert "old_driver" not in result["tracked_roster"]
        assert result["current_driver_points"]["old_driver"] == 25.0


class TestBatchScoreDrivers:
    def test_scores_every_driver_in_the_given_rows(self):
        model_card = {"feature_columns": ["f1"], "algorithm": "xgboost_regressor"}
        driver_rows = {"a": {"f1": 1.0}, "b": {"f1": 2.0}}
        fake_adapter = MagicMock()
        fake_adapter.predict.return_value = [3.0, 5.0]
        with patch.dict(season_projection.ADAPTERS, {"xgboost_regressor": fake_adapter}):
            result = season_projection._batch_score_drivers(MagicMock(), model_card, driver_rows)
        assert result == {"a": 3.0, "b": 5.0}


class TestBuildSeasonProjection:
    def test_no_event_ever_stored_returns_none(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([])
        assert season_projection.build_season_projection(storage, MagicMock(), MagicMock()) is None

    def test_nothing_remaining_uses_real_final_standings_not_a_simulation(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2024-03-02", 2024, participants=[
                _participant("a", 1, constructor_entity_id="red_bull"), _participant("b", 2, constructor_entity_id="mercedes"),
            ]),
        ])
        storage.get_entity.return_value = None

        result = season_projection.build_season_projection(storage, MagicMock(), MagicMock())

        assert result["simulations"] == 0
        champion_row = next(r for r in result["driver_standings"] if r["entity_id"] == "a")
        assert champion_row["champion_probability"] == 1.0
        assert result["constructor_standings"][0]["entity_id"] == "red_bull"

    def test_remaining_races_are_scored_and_handed_to_the_simulation(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = _by_status([
            _field_event("1", "2024-03-02", 2024, participants=[_participant("a", 1)]),
            _field_event("2", "2024-03-09", 2024, status="scheduled"),
        ])
        storage.get_entity.return_value = {"name": "Driver"}
        model_card = {"feature_columns": ["f1"], "algorithm": "xgboost_regressor", "rmse": 2.0}
        fake_adapter = MagicMock()
        fake_adapter.predict.return_value = [1.0]

        with patch.object(season_projection.model_loader, "load_current_model", return_value=(MagicMock(), model_card)), \
             patch.dict(season_projection.ADAPTERS, {"xgboost_regressor": fake_adapter}), \
             patch.object(season_projection.live_features, "build_projected_field_features", return_value={"a": {"f1": 1.0}}), \
             patch.object(season_projection.season_simulation, "simulate_season", return_value={
                 "driver_standings": [{"entity_id": "a", "current_points": 25.0, "projected_points": 50.0, "champion_probability": 1.0}],
                 "constructor_standings": [{"entity_id": "red_bull", "current_points": 25.0, "projected_points": 50.0, "champion_probability": 1.0}],
                 "simulations": 750,
             }) as mock_sim:
            result = season_projection.build_season_projection(storage, MagicMock(), MagicMock())

        assert result["simulations"] == 750
        assert result["driver_standings"][0]["entity_id"] == "a"
        mock_sim.assert_called_once()


class TestRunScheduled:
    def test_writes_to_s3_when_a_projection_is_produced(self):
        s3 = MagicMock()
        with patch.object(season_projection, "build_season_projection", return_value={"season": 2026, "driver_standings": [], "constructor_standings": []}):
            season_projection.run_scheduled(MagicMock(), s3, MagicMock())
        s3.put_json.assert_called_once()

    def test_skips_the_s3_write_when_there_is_no_season_to_project(self):
        s3 = MagicMock()
        with patch.object(season_projection, "build_season_projection", return_value=None):
            result = season_projection.run_scheduled(MagicMock(), s3, MagicMock())
        s3.put_json.assert_not_called()
        assert result == {"sport": "f1", "skipped": True}
