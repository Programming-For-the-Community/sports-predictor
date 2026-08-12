"""
Unit tests for the NCAAFB inference Lambda's scheduled season-projection
path: season_projection._season_standings_inputs (wins/losses/ties/point
differential/conference/Elo derivation from stored events) and the
EventBridge-triggered ScheduledSeasonProjection handler branch that runs
season_simulation.simulate_season and writes the result to S3. Mirrors
tests/aws-lambdas/nfl/test_predict_season_simulation.py's own shape --
NOT a port of it, since _season_standings_inputs here also derives each
team's conference (no static division table -- see season_simulation.
py's own docstring) and the ScheduledSeasonProjection branch has an
extra step NFL's never needed: loading the real national-ranking model
before simulate_season can even be called (see season_projection.py's
own docstring for why this needs the real model rather than a cheap
proxy).

The ncaafb_predict module is registered in sys.modules by conftest.py,
whose reset_ncaafb_predict_singletons fixture (autouse) resets
ncaafb_predict._storage/_model_bucket/_predictions_table before and after
every test here.
"""
from unittest.mock import MagicMock, patch

import model_loader
import ncaafb_predict
import season_projection
import season_simulation


def _completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                      event_id=None, event_date="2025-09-14", season_type="regular", week=1,
                      home_conference="Big Ten", away_conference="SEC"):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "completed",
        "home_conference": home_conference, "away_conference": away_conference,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


def _scheduled_event(event_key, season, event_date, home_id, away_id, *,
                      event_id=None, season_type="regular", week=1,
                      home_conference="Big Ten", away_conference="SEC"):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "week": week, "status": "scheduled",
        "home_conference": home_conference, "away_conference": away_conference,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }


def _model_card(version: int) -> dict:
    return {"version": version, "algorithm": "fake", "feature_columns": []}


class TestSeasonStandingsInputs:
    def test_derives_wins_losses_and_point_differential_from_completed_events(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["wins"]["12"] == 1
        assert inputs["losses"]["24"] == 1
        assert inputs["point_differential"]["12"] == 7
        assert inputs["point_differential"]["24"] == -7

    def test_a_tie_counts_toward_neither_teams_wins_or_losses(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 20, 20)],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["ties"]["12"] == 1
        assert inputs["ties"]["24"] == 1
        assert inputs["wins"].get("12", 0) == 0
        assert inputs["losses"].get("12", 0) == 0

    def test_current_season_is_the_max_season_across_both_statuses(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2024, "12", "24", 27, 20)],
            "scheduled": [_scheduled_event("E2", 2025, "2025-09-21", "12", "7")],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["current_season"] == 2025
        # The 2024 completed game shouldn't leak into this season's record.
        assert inputs["wins"] == {}

    def test_team_conference_comes_from_each_events_own_conference_fields(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20, home_conference="Big Ten", away_conference="SEC")],
            "scheduled": [],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["team_conference"]["12"] == "Big Ten"
        assert inputs["team_conference"]["24"] == "SEC"

    def test_remaining_games_excludes_a_pairing_missing_a_conference(self):
        # "7" (away) has no conference -- an FCS buy game, see this
        # module's own docstring for why it's excluded from the walk-
        # forward simulation entirely rather than simulated against a
        # meaningless default rating.
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [_scheduled_event("E1", 2025, "2025-09-21", "12", "7", away_conference=None)],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["remaining_games"] == []
        # team_next_event still reflects the real schedule regardless.
        assert inputs["team_next_event"]["12"] == "E1"

    def test_remaining_games_and_team_next_event_reflect_chronological_order(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [],
            "scheduled": [
                _scheduled_event("E2", 2025, "2025-09-28", "12", "25"),
                _scheduled_event("E1", 2025, "2025-09-21", "12", "7"),
            ],
        }[status]

        inputs = season_projection._season_standings_inputs(storage)

        assert inputs["remaining_games"] == [("12", "7"), ("12", "25")]
        assert inputs["team_next_event"]["12"] == "E1"  # earliest game, not insertion order
        assert inputs["games_remaining"]["12"] == 2


class TestScheduledSeasonProjection:
    """GET /ncaafb/season is served from predict-read/handler.py, not
    here -- this Lambda instead computes the projection on Terraform/
    scheduler-ncaafb-season-projection.tf's weekly direct EventBridge
    Scheduler invoke and writes it to S3, same split as NFL's own
    predict/season_projection.py."""

    # 12 teams across 3 conferences -- season_simulation.simulate_season
    # requires >= CFP_FIELD_SIZE (12) tracked teams before it'll even
    # attempt a CFP field, same guard build_season_projection itself
    # checks before bothering to load the ranking model at all.
    TWELVE_TEAM_EVENTS = [
        _completed_event(f"E{i}", 2025, f"t{2*i}", f"t{2*i+1}", 27, 20, home_conference=f"C{i % 3}", away_conference=f"C{(i + 1) % 3}")
        for i in range(6)
    ]

    def test_writes_the_season_projection_to_s3_under_the_expected_key(self):
        ncaafb_predict._storage = MagicMock()
        ncaafb_predict._model_bucket = MagicMock()
        ncaafb_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]

        with patch.object(season_simulation, "simulate_season", return_value={}):
            response = ncaafb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        ncaafb_predict._model_bucket.put_json.assert_called_once()
        key, body = ncaafb_predict._model_bucket.put_json.call_args[0]
        assert key == "season-projections/ncaafb/latest.json"
        assert body["season"] == 2025
        assert body["standings"][0]["wins"] == 1
        assert body["standings"][0]["conference"] == "Big Ten"
        # Team outcomes only -- see this module's own docstring for why
        # NCAAFB's season projection has no player-prop leaderboard,
        # unlike NFL's.
        assert "leaderboards" not in body

    def test_fewer_than_twelve_tracked_teams_skips_simulation_but_still_writes_standings(self):
        ncaafb_predict._storage = MagicMock()
        ncaafb_predict._model_bucket = MagicMock()
        ncaafb_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": [_completed_event("E1", 2025, "12", "24", 27, 20)],
            "scheduled": [],
        }[status]

        with patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))) as load_current_model, \
             patch.object(season_simulation, "simulate_season") as simulate_season:
            ncaafb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        simulate_season.assert_not_called()
        ranking_calls = [call for call in load_current_model.call_args_list if call.args[2] == season_projection.RANKING_MODEL_NAME]
        assert ranking_calls == []
        body = ncaafb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"][0]["wins"] == 1
        assert "projected_wins" not in body["standings"][0]

    def test_no_promoted_ranking_model_skips_simulation_but_still_writes_standings(self):
        ncaafb_predict._storage = MagicMock()
        ncaafb_predict._model_bucket = MagicMock()
        ncaafb_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": self.TWELVE_TEAM_EVENTS,
            "scheduled": [],
        }[status]

        with patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("nope")):
            response = ncaafb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaafb_predict._model_bucket.put_json.call_args[0][1]
        assert "projected_wins" not in body["standings"][0]

    def test_simulation_runs_and_standings_include_projected_fields_when_enough_teams_are_tracked(self):
        ncaafb_predict._storage = MagicMock()
        ncaafb_predict._model_bucket = MagicMock()
        ncaafb_predict._storage.get_all_events.side_effect = lambda sport, status: {
            "completed": self.TWELVE_TEAM_EVENTS,
            "scheduled": [],
        }[status]

        simulated = {f"t{i}": {"projected_wins": 8.0, "conference_champion_probability": 0.1, "bowl_probability": 0.6, "playoff_probability": 0.2, "championship_probability": 0.01} for i in range(12)}

        with patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(season_simulation, "simulate_season", return_value=simulated) as simulate_season:
            ncaafb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        simulate_season.assert_called_once()
        body = ncaafb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"][0]["projected_wins"] == 8.0
