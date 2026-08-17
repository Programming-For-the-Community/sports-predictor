"""
Unit tests for the season tab's CFP-bracket reconciliation
(season_projection._resolve_matchup/_bracket_payload) -- same 3-state
design as NFL's own tests/aws-lambdas/nfl/test_predict_bracket.py
(projected/scheduled/final), adapted for NCAAFB's 12-team topology and
ranking-model-driven seeding. Not a port -- NCAAFB's own _bracket_payload
signature takes an estimator/model_card/teams (see that function's own
docstring for why seeding always uses TODAY's real ranking-model score,
never a simulated future one).
"""
from unittest.mock import MagicMock, patch

import event_prediction
import season_projection
import season_simulation

# 12 distinct conferences, one team each, so every team is its own
# conference champion and _select_cfp_field's auto-bid logic picks all 12
# by construction -- avoids needing to hand-construct a realistic
# multi-team-per-conference field just to exercise the bracket walk.
TEAMS = [f"t{i}" for i in range(12)]
TEAM_CONFERENCE = {team_id: f"conf{i}" for i, team_id in enumerate(TEAMS)}


def _model_card() -> dict:
    return {"version": 1, "algorithm": "fake", "feature_columns": []}


def _postseason_event(event_key, season, home_id, away_id, *, status="scheduled",
                       home_score=None, away_score=None, event_id=None):
    participants = [
        {"entity_id": home_id, "role": "home", "result": None if home_score is None else {"score": home_score, "won": home_score > away_score}},
        {"entity_id": away_id, "role": "away", "result": None if away_score is None else {"score": away_score, "won": away_score > home_score}},
    ]
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": "2026-01-03",
        "season": season, "is_playoff_game": True, "week": 16,
        "status": status, "participants": participants,
    }


class TestResolveMatchup:
    def test_no_real_game_falls_back_to_the_deterministic_projection(self):
        result = season_projection._resolve_matchup(
            "a", "b", 1, 2, {}, MagicMock(), MagicMock(), MagicMock(), {"a": 1900, "b": 1400},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "projected"
        assert result["predicted_winner"] == "a"

    def test_real_completed_game_reports_the_actual_winner_and_score(self):
        event = _postseason_event("E1", 2025, "t0", "t1", status="completed", home_score=31, away_score=17)
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._resolve_matchup(
            "t0", "t1", 1, 8, {frozenset(("t0", "t1")): event}, MagicMock(), MagicMock(), predictions_table, {},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "final"
        assert result["actual_winner"] == "t0"
        assert result["actual_home_score"] == 31

    def test_real_scheduled_game_with_no_logged_prediction_computes_one_in_process(self):
        event = _postseason_event("E1", 2025, "t0", "t1", status="scheduled")
        predictions_table = MagicMock()
        predictions_table.query.side_effect = [
            [],
            [{"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.55}}],
        ]

        with patch.object(event_prediction, "compute_and_cache_event") as compute:
            result = season_projection._resolve_matchup(
                "t0", "t1", 1, 8, {frozenset(("t0", "t1")): event}, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        compute.assert_called_once()
        assert result["status"] == "scheduled"
        assert result["predicted_winner"] == "t0"
        assert result["win_probability"] == 0.55


class TestBracketPayload:
    def test_returns_none_when_fewer_than_twelve_teams_are_tracked(self):
        result = season_projection._bracket_payload(
            MagicMock(), MagicMock(), MagicMock(),
            {"current_season": 2025, "wins": {}, "point_differential": {}, "current_ratings": {}, "team_conference": {}},
            MagicMock(), _model_card(), ["t0", "t1"],
        )

        assert result is None

    def test_returns_four_rounds_and_a_champion_when_fully_projected(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []  # no real CFP games yet
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        season_inputs = {
            "current_season": 2025,
            "wins": {team_id: 10 for team_id in TEAMS},
            "losses": {team_id: 2 for team_id in TEAMS},
            "point_differential": {team_id: 0 for team_id in TEAMS},
            "current_ratings": {},
            "team_conference": TEAM_CONFERENCE,
        }

        with patch.object(season_projection, "_batch_score_teams", return_value={team_id: i for i, team_id in enumerate(TEAMS)}):
            result = season_projection._bracket_payload(
                storage, MagicMock(), predictions_table, season_inputs, MagicMock(), _model_card(), TEAMS,
            )

        assert [r["round"] for r in result["rounds"]] == ["Round of 12", "Quarterfinals", "Semifinals", "National Championship"]
        assert len(result["rounds"][0]["matchups"]) == 4
        assert result["champion"] in TEAMS

    def test_a_real_completed_cfp_game_is_reflected_in_the_bracket(self):
        # model_scores gives t0..t11 seeds 1-12 in order (lower score is
        # better -- see _batch_score_teams' own convention), so the Round
        # of 12's 5v12 slot is deterministically t4 vs t11.
        home_id, away_id = "t4", "t11"
        real_game = _postseason_event("E1", 2025, home_id, away_id, status="completed", home_score=24, away_score=20)
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: [real_game] if status == "completed" else []
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        season_inputs = {
            "current_season": 2025,
            "wins": {team_id: 10 for team_id in TEAMS},
            "losses": {team_id: 2 for team_id in TEAMS},
            "point_differential": {team_id: 0 for team_id in TEAMS},
            "current_ratings": {},
            "team_conference": TEAM_CONFERENCE,
        }

        with patch.object(season_projection, "_batch_score_teams", return_value={team_id: i for i, team_id in enumerate(TEAMS)}):
            result = season_projection._bracket_payload(
                storage, MagicMock(), predictions_table, season_inputs, MagicMock(), _model_card(), TEAMS,
            )

        round_of_12 = result["rounds"][0]["matchups"]
        real_matchup = next((m for m in round_of_12 if {m["team_a"], m["team_b"]} == {home_id, away_id}), None)
        assert real_matchup is not None
        assert real_matchup["status"] == "final"
        assert real_matchup["actual_winner"] == home_id
