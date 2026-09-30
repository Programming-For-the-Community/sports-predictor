"""
Unit tests for library.serving.bracket_projection -- the bracket slot
resolution every head-to-head sport's season_projection.py shares. Each
sport's own test_predict_bracket.py covers it through that sport's wiring.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from library.serving import bracket_projection


def _event(home: str, away: str, status: str = "scheduled", **extra) -> dict:
    return {
        "event_key": f"EVENT#{home}-{away}", "event_id": f"{home}-{away}", "status": status,
        "participants": [
            {"entity_id": home, "role": "home", "result": extra.pop("home_result", None)},
            {"entity_id": away, "role": "away", "result": extra.pop("away_result", None)},
        ],
        **extra,
    }


def _logged_row(home_win_probability: float) -> dict:
    return {
        "model_key": "MODEL#win-probability#v1", "generated_at": "2026-01-01T00:00:00+00:00",
        "predicted_value": {"home_win_probability": home_win_probability},
    }


def _resolver(project_matchup=None, compute_and_cache_event=None) -> bracket_projection.BracketResolver:
    return bracket_projection.BracketResolver(
        event_prediction=SimpleNamespace(compute_and_cache_event=compute_and_cache_event or MagicMock()),
        season_simulation=SimpleNamespace(project_matchup=project_matchup or MagicMock()),
        logger=logging.getLogger("test-bracket"),
    )


class TestRealPostseasonMatchups:
    def test_keeps_only_this_seasons_postseason_games_keyed_by_the_pair(self):
        storage = MagicMock()
        playoff = _event("a", "b", season=2026, playoff=True)
        storage.get_all_events.side_effect = lambda sport, status: {
            "scheduled": [playoff, _event("c", "d", season=2026, playoff=False)],
            "completed": [_event("e", "f", season=2025, playoff=True), {"season": 2026, "playoff": True, "participants": []}],
        }[status]

        result = bracket_projection.real_postseason_matchups(storage, "nfl", 2026, lambda e: e["playoff"])

        assert result == {frozenset(("a", "b")): playoff}
        storage.get_all_events.assert_any_call("nfl", status="completed")


class TestPredictedWinnerAndProbability:
    def test_none_when_nothing_was_logged(self):
        assert bracket_projection.predicted_winner_and_probability(None, "h", "a") == (None, None)

    def test_home_favored(self):
        assert bracket_projection.predicted_winner_and_probability({"home_win_probability": 0.7}, "h", "a") == ("h", 0.7)

    def test_away_favored_reports_the_away_sides_probability(self):
        winner, probability = bracket_projection.predicted_winner_and_probability({"home_win_probability": 0.25}, "h", "a")
        assert winner == "a"
        assert probability == pytest.approx(0.75)


class TestResolveMatchup:
    def test_a_bye_advances_team_a_outright(self):
        matchup = _resolver().resolve_matchup("t1", None, 1, None, {}, None, None, None, {}, 0.0)

        assert matchup["status"] == "projected"
        assert matchup["predicted_winner"] == "t1"
        assert matchup["win_probability"] == pytest.approx(1.0)

    def test_no_real_game_uses_the_sports_own_projection(self):
        project = MagicMock(return_value={"team_a": "t1", "team_b": "t2", "predicted_winner": "t2"})

        matchup = _resolver(project_matchup=project).resolve_matchup("t1", "t2", 1, 8, {}, None, None, None, {"t1": 1500}, 2.5)

        project.assert_called_once_with("t1", "t2", 1, 8, {"t1": 1500}, 2.5)
        assert matchup["status"] == "projected"

    def test_a_completed_real_game_is_final_with_its_actual_result(self):
        event = _event("t1", "t2", "completed", home_result={"score": 24, "won": True}, away_result={"score": 17, "won": False})
        table = MagicMock()
        table.query.return_value = [_logged_row(0.4)]

        matchup = _resolver().resolve_matchup("t2", "t1", 8, 1, {frozenset(("t1", "t2")): event}, None, None, table, {}, 0.0)

        assert matchup["status"] == "final"
        assert matchup["actual_winner"] == "t1"
        assert (matchup["actual_home_score"], matchup["actual_away_score"]) == (24, 17)
        assert matchup["predicted_winner"] == "t2"

    def test_a_scheduled_real_game_computes_its_prediction_when_none_was_logged(self):
        event = _event("t1", "t2")
        table = MagicMock()
        table.query.side_effect = [[], [_logged_row(0.6)]]
        compute = MagicMock()

        matchup = _resolver(compute_and_cache_event=compute).resolve_matchup(
            "t1", "t2", 1, 8, {frozenset(("t1", "t2")): event}, "storage", "s3", table, {}, 0.0,
        )

        compute.assert_called_once_with("storage", "s3", table, "t1-t2")
        assert matchup["status"] == "scheduled"
        assert matchup["predicted_winner"] == "t1"

    def test_a_failed_live_prediction_still_returns_the_scheduled_row(self, caplog):
        table = MagicMock()
        table.query.return_value = []

        with caplog.at_level(logging.ERROR, logger="test-bracket"):
            row = _resolver(compute_and_cache_event=MagicMock(side_effect=RuntimeError("boom"))).scheduled_matchup_row(
                _event("t1", "t2"), "EVENT#t1-t2", "t1", "t2", 1, 8, None, None, table,
            )

        assert row["predicted_winner"] is None
        assert "Failed computing a live prediction" in caplog.text


class TestProjectBracketRound:
    def test_advances_each_slots_winner_with_its_own_seed(self):
        project = MagicMock(side_effect=lambda a, b, *rest: {"team_a": a, "team_b": b, "predicted_winner": b})
        completed = _event("t3", "t4", "completed", home_result={"score": 10, "won": True}, away_result={"score": 3, "won": False})
        table = MagicMock()
        table.query.return_value = []

        round_payload, advancing = _resolver(project_matchup=project).project_bracket_round(
            "Wild Card", [("t1", "t2", 1, 8), ("t3", "t4", 4, 5), ("t5", None, 2, None)],
            {frozenset(("t3", "t4")): completed}, None, None, table, {}, 0.0,
        )

        assert round_payload["round"] == "Wild Card"
        assert len(round_payload["matchups"]) == 3
        assert advancing == [("t2", 8), ("t3", 4), ("t5", 2)]
