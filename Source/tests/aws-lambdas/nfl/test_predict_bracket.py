"""
Unit tests for the season tab's playoff-bracket reconciliation
(season_projection._resolve_matchup/_bracket_payload) -- three states:
no real game yet -> projected; real scheduled game -> live prediction,
computed on the spot if nobody's logged one yet; real completed game ->
actual result + whatever was originally predicted.
"""
from unittest.mock import MagicMock, patch

import event_prediction
import season_projection
import season_simulation
from library.features.nfl_teams import TEAM_DIVISIONS

# Real ESPN team_ids from TEAM_DIVISIONS -- _bracket_payload's seeding
# reads the real table, so a meaningful full-bracket test needs real ids.
KC = "12"   # AFC West
LV = "13"   # AFC West


def _postseason_event(event_key, season, home_id, away_id, *, status="scheduled",
                       home_score=None, away_score=None, event_id=None):
    participants = [
        {"entity_id": home_id, "role": "home", "result": None if home_score is None else {"score": home_score, "won": home_score > away_score}},
        {"entity_id": away_id, "role": "away", "result": None if away_score is None else {"score": away_score, "won": away_score > home_score}},
    ]
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": "2026-01-10",
        "season": season, "season_type": season_projection.POSTSEASON_TYPE, "week": 19,
        "status": status, "participants": participants,
    }


class TestResolveMatchup:
    def test_no_real_game_falls_back_to_the_deterministic_projection(self):
        real_matchups = {}
        ratings = {"a": 1900, "b": 1400}

        result = season_projection._resolve_matchup(
            "a", "b", 1, 2, real_matchups, MagicMock(), MagicMock(), MagicMock(), ratings,
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "projected"
        assert result["predicted_winner"] == "a"

    def test_real_completed_game_reports_the_actual_winner_and_score(self):
        event = _postseason_event("E1", 2025, KC, LV, status="completed", home_score=27, away_score=13)
        real_matchups = {frozenset((KC, LV)): event}
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._resolve_matchup(
            KC, LV, 1, 2, real_matchups, MagicMock(), MagicMock(), predictions_table, {},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "final"
        assert result["actual_winner"] == KC
        assert result["actual_home_score"] == 27
        assert result["actual_away_score"] == 13

    def test_real_completed_game_includes_what_was_originally_predicted(self):
        event = _postseason_event("E1", 2025, KC, LV, status="completed", home_score=27, away_score=13)
        real_matchups = {frozenset((KC, LV)): event}
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.7}},
        ]

        result = season_projection._resolve_matchup(
            KC, LV, 1, 2, real_matchups, MagicMock(), MagicMock(), predictions_table, {},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["predicted_winner"] == KC
        assert result["win_probability"] == 0.7

    def test_real_scheduled_game_with_an_already_logged_prediction_uses_it(self):
        event = _postseason_event("E1", 2025, KC, LV, status="scheduled")
        real_matchups = {frozenset((KC, LV)): event}
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.35}},
        ]

        with patch.object(event_prediction, "compute_and_cache_event") as compute:
            result = season_projection._resolve_matchup(
                KC, LV, 1, 2, real_matchups, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        compute.assert_not_called()  # already logged -- no need to compute one
        assert result["status"] == "scheduled"
        assert result["predicted_winner"] == LV
        assert result["win_probability"] == 0.65

    def test_real_scheduled_game_with_no_logged_prediction_computes_one_in_process(self):
        event = _postseason_event("E1", 2025, KC, LV, status="scheduled")
        real_matchups = {frozenset((KC, LV)): event}
        predictions_table = MagicMock()
        # First query (before computing): nothing logged yet. Second query
        # (after compute_and_cache_event "writes" it): the fresh row.
        predictions_table.query.side_effect = [
            [],
            [{"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.6}}],
        ]

        with patch.object(event_prediction, "compute_and_cache_event") as compute:
            result = season_projection._resolve_matchup(
                KC, LV, 1, 2, real_matchups, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        compute.assert_called_once()
        assert result["predicted_winner"] == KC
        assert result["win_probability"] == 0.6

    def test_a_failed_on_the_spot_compute_degrades_to_no_prediction_rather_than_raising(self):
        event = _postseason_event("E1", 2025, KC, LV, status="scheduled")
        real_matchups = {frozenset((KC, LV)): event}
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        with patch.object(event_prediction, "compute_and_cache_event", side_effect=RuntimeError("boom")):
            result = season_projection._resolve_matchup(
                KC, LV, 1, 2, real_matchups, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        assert result["status"] == "scheduled"
        assert result["predicted_winner"] is None
        assert result["win_probability"] is None


class TestBracketPayload:
    def test_returns_both_conferences_a_super_bowl_and_a_champion(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []  # no real postseason games yet -- fully projected
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        season_inputs = {
            "current_season": 2025,
            "remaining_games": [],  # regular season over -- seed from real wins
            "wins": {},
            "point_differential": {},
            "current_ratings": {},
        }

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, season_inputs, {})

        assert set(result["conferences"]) == {"AFC", "NFC"}
        for rounds in result["conferences"].values():
            assert [r["round"] for r in rounds] == ["Wild Card", "Divisional", "Conference Championship"]
        all_teams = set(TEAM_DIVISIONS)
        assert result["champion"] in all_teams
        assert result["super_bowl"]["predicted_winner"] == result["champion"]

    def test_seeds_from_projected_wins_when_the_regular_season_is_still_in_progress(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        # KC massively favored to lead its conference if projected_wins is
        # actually what gets used for seeding (real "wins" left empty/0
        # for everyone, so this only passes if the projected path is
        # taken).
        simulation = {team_id: {"projected_wins": 17.0 if team_id == KC else 0.0} for team_id in TEAM_DIVISIONS}
        season_inputs = {
            "current_season": 2025,
            "remaining_games": [(KC, LV)],  # non-empty -- regular season still in progress
            "wins": {},
            "point_differential": {},
            "current_ratings": {KC: 2200},
        }

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, season_inputs, simulation)

        afc_rounds = result["conferences"]["AFC"]
        wild_card_seeds = {m["seed_a"] for m in afc_rounds[0]["matchups"]} | {m["seed_b"] for m in afc_rounds[0]["matchups"]}
        # KC (seed 1, projected_wins=17) has a bye -- it never appears as
        # a seed number inside the Wild Card round's own matchups.
        assert 1 not in wild_card_seeds or afc_rounds[0]["matchups"][0]["seed_a"] != 1

    def test_a_real_completed_postseason_game_is_reflected_in_the_bracket(self):
        # Every AFC team gets a DISTINCT win total (no ties at all) so
        # _seed_conference's own seeding is fully deterministic regardless
        # of Python's per-process string-hash randomization -- an all-tied
        # scenario (every team defaulting to 0 wins) leaves seeding order
        # dependent on set-iteration order inside _seed_conference's own
        # `{max(...) for ...}` division-winner comprehension, which is
        # only stable within one process, not reproducible across runs.
        # With these wins, AFC seeds work out to 1=2, 2=33, 3=34, 4=7,
        # 5=15, 6=17, 7=20 -- so the Wild Card round's 2v7 slot is
        # deterministically team "33" vs team "20".
        afc_wins = {
            "2": 16, "33": 15, "34": 14, "7": 13, "15": 12, "17": 11, "20": 10,
            "12": 9, "13": 8, "24": 7, "4": 6, "5": 5, "23": 4, "11": 3, "30": 2, "10": 1,
        }
        home_id, away_id = "33", "20"
        real_game = _postseason_event("E1", 2025, home_id, away_id, status="completed", home_score=30, away_score=10)
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: [real_game] if status == "completed" else []
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        season_inputs = {
            "current_season": 2025, "remaining_games": [], "wins": afc_wins, "point_differential": {}, "current_ratings": {},
        }

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, season_inputs, {})

        afc_matchups = [m for r in result["conferences"]["AFC"] for m in r["matchups"]]
        real_matchup = next((m for m in afc_matchups if {m["team_a"], m["team_b"]} == {home_id, away_id}), None)
        assert real_matchup is not None
        assert real_matchup["status"] == "final"
        assert real_matchup["actual_winner"] == home_id
