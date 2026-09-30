"""
Edge cases for NCAAFB's season_projection: malformed/partial events are
skipped, a failed live bracket prediction leaves the slot unpredicted, and
the ranking model scores every team in one batched call.
"""
import math
from unittest.mock import MagicMock, patch

import season_projection
import season_simulation
from library.ml import model_types

_NO_SIDES = {"event_key": "X", "event_id": "X", "season": 2025, "is_playoff_game": True, "status": "completed", "participants": []}


def _game(event_key, home_id, away_id, home_score, away_score, status="completed", **extra):
    return {
        "event_key": event_key, "event_id": event_key, "event_date": "2025-10-04", "season": 2025, "status": status,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score}},
        ],
        **extra,
    }


class TestMalformedEventsAreSkipped:
    def test_completed_records_skip_missing_sides_and_scores(self):
        wins, losses, *_ = season_projection._completed_game_records(
            [_NO_SIDES, _game("E2", "a", "b", None, 3), _game("E1", "a", "b", 28, 21)],
        )

        assert wins == {"a": 1, "b": 0}
        assert losses == {"a": 0, "b": 1}

    def test_remaining_games_skip_missing_sides(self):
        remaining, next_event = season_projection._remaining_game_inputs(
            [{**_NO_SIDES, "status": "scheduled"}, _game("S1", "a", "b", None, None, status="scheduled")], {"a": "SEC", "b": "SEC"},
        )

        assert remaining == [("a", "b")]
        assert next_event == {"a": "S1", "b": "S1"}

    def test_real_current_ranks_skip_missing_sides(self):
        ranks = season_projection._real_current_ranks([_NO_SIDES, _game("E1", "a", "b", 1, 0, home_current_rank=4)])

        assert ranks == {"a": 4}

    def test_real_postseason_matchups_skip_missing_sides(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: (
            [_NO_SIDES, _game("P1", "a", "b", 30, 20, is_playoff_game=True)] if status == "completed" else []
        )

        assert list(season_projection._real_postseason_matchups(storage, 2025)) == [frozenset(("a", "b"))]


class TestScheduledMatchupComputeFailure:
    def test_a_failed_live_prediction_leaves_the_slot_unpredicted_instead_of_raising(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        with patch.object(season_projection.event_prediction, "compute_and_cache_event", side_effect=RuntimeError("boom")):
            row = season_projection._scheduled_matchup_row(
                {"event_id": "E1"}, "EVENT#E1", "a", "b", 1, 8, MagicMock(), MagicMock(), predictions_table,
            )

        assert row["status"] == "scheduled"
        assert row["predicted_winner"] is None
        assert row["win_probability"] is None


class TestBatchScoreTeams:
    def test_scores_every_team_in_one_call_with_nan_for_missing_features(self):
        season_inputs = {
            "avg_points_scored": {"a": 31.0}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
            "current_season": 2025,
        }
        adapter = MagicMock()
        adapter.predict.return_value = [0.9, 0.2]
        model_card = {"algorithm": "fake", "feature_columns": ["wins", "avg_points_scored"]}

        with patch.dict(model_types.ADAPTERS, {"fake": adapter}):
            scores = season_projection._batch_score_teams(
                "estimator", model_card, ["a", "b"], season_inputs, {"a": 5}, {"b": 2}, {},
            )

        assert scores == {"a": 0.9, "b": 0.2}
        adapter.predict.assert_called_once()
        features = adapter.predict.call_args.args[1]
        assert features.loc["a"].tolist() == [5.0, 31.0]
        assert features.loc["b", "wins"] == 0.0
        assert math.isnan(features.loc["b", "avg_points_scored"])

    def test_season_simulation_scores_teams_through_the_ranking_model(self):
        teams = [f"t{i}" for i in range(season_simulation.CFP_FIELD_SIZE)]
        season_inputs = {
            "team_conference": {team: "SEC" for team in teams}, "wins": {}, "losses": {}, "ties": {},
            "point_differential": {}, "remaining_games": [], "current_ratings": {}, "real_current_rank": {},
            "current_season": 2025,
        }
        scored = {}

        def _simulate(wins, losses, point_differential, remaining_games, ratings, team_conference, score_teams):
            scored.update(score_teams(wins, losses, ratings))
            return {}

        with patch.object(season_projection, "_season_standings_inputs", return_value=season_inputs), \
             patch.object(season_projection.model_loader, "load_current_model", return_value=("estimator", {"algorithm": "x"})), \
             patch.object(season_projection, "_batch_score_teams", return_value={"t0": 1.0}) as batch_score, \
             patch.object(season_simulation, "simulate_season", side_effect=_simulate), \
             patch.object(season_projection, "_model_rankings", return_value={}), \
             patch.object(season_projection, "_bracket_payload", return_value=None), \
             patch.object(season_projection, "enrich_team_standings", side_effect=lambda storage, sport, rows: rows):
            season_projection.build_season_projection(MagicMock(), MagicMock(), MagicMock())

        assert scored == {"t0": 1.0}
        assert batch_score.call_args.args[:4] == ("estimator", {"algorithm": "x"}, teams, season_inputs)
