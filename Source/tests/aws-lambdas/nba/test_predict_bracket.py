"""
Unit tests for the season tab's playoff-bracket reconciliation
(season_projection._resolve_matchup/_bracket_payload) -- same 3-state
design as NFL's/NCAAFB's own test_predict_bracket.py, adapted for NBA's
play-in-aware topology. Real ESPN team ids from library.features.
nba_teams.TEAM_DIVISIONS -- _bracket_payload's seeding reads the real
table via season_simulation._teams_by_conference, so a meaningful
full-bracket test needs real ids.

With every team's wins/point_differential left at their 0 default,
Eastern conference seeding is fully deterministic (NBA's own
_seed_conference sorts the SAME list _teams_by_conference already builds
from TEAM_DIVISIONS' own dict-insertion order -- no set-comprehension tie-
break the way NFL's own _seed_conference has, confirmed stable across
PYTHONHASHSEED values): seeds 1-6 = 2,17,18,20,28,4; seeds 7-10 =
5,8,11,15. With equal ratings, every play-in game is won by the better
(numerically lower) seed, so the resolved 8-team field is
2,17,18,20,28,4,5,8 and the Conference Quarterfinals pairs are (2,8)/(20,28)/(18,4)/
(17,5).
"""
from unittest.mock import MagicMock, patch

import event_prediction
import season_projection
import season_simulation

SEED_1, SEED_2, SEED_3, SEED_4, SEED_5, SEED_6 = "2", "17", "18", "20", "28", "4"
SEED_7, SEED_8, SEED_9, SEED_10 = "5", "8", "11", "15"


def _postseason_event(event_key, season, home_id, away_id, *, status="scheduled",
                       home_score=None, away_score=None, event_id=None, season_type=3):
    participants = [
        {"entity_id": home_id, "role": "home", "result": None if home_score is None else {"score": home_score, "won": home_score > away_score}},
        {"entity_id": away_id, "role": "away", "result": None if away_score is None else {"score": away_score, "won": away_score > home_score}},
    ]
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": "2026-04-20",
        "season": season, "season_type": season_type, "status": status, "participants": participants,
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
        event = _postseason_event("E1", 2026, SEED_1, SEED_8, status="completed", home_score=112, away_score=98)
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._resolve_matchup(
            SEED_1, SEED_8, 1, 8, {frozenset((SEED_1, SEED_8)): event}, MagicMock(), MagicMock(), predictions_table, {},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "final"
        assert result["actual_winner"] == SEED_1
        assert result["actual_home_score"] == 112

    def test_real_scheduled_game_with_no_logged_prediction_computes_one_in_process(self):
        event = _postseason_event("E1", 2026, SEED_1, SEED_8, status="scheduled")
        predictions_table = MagicMock()
        predictions_table.query.side_effect = [
            [],
            [{"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.62}}],
        ]

        with patch.object(event_prediction, "compute_and_cache_event") as compute:
            result = season_projection._resolve_matchup(
                SEED_1, SEED_8, 1, 8, {frozenset((SEED_1, SEED_8)): event}, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        compute.assert_called_once()
        assert result["status"] == "scheduled"
        assert result["predicted_winner"] == SEED_1
        assert result["win_probability"] == 0.62


class TestSeriesRecord:
    def test_counts_wins_by_team_id_not_home_away_role(self):
        # A real series' host alternates by game (2-2-1-1-1) -- team_a
        # wins game 1 at home, then wins game 3 on the road.
        game1 = _postseason_event("E1", 2026, SEED_1, SEED_8, status="completed", home_score=110, away_score=100)
        game2 = _postseason_event("E2", 2026, SEED_1, SEED_8, status="completed", home_score=90, away_score=100)
        game3 = _postseason_event("E3", 2026, SEED_8, SEED_1, status="completed", home_score=95, away_score=105)

        wins_a, wins_b = season_projection._series_record(SEED_1, SEED_8, [game1, game2, game3])

        assert wins_a == 2  # game1 (home) + game3 (road)
        assert wins_b == 1  # game2

    def test_a_scheduled_unplayed_game_contributes_nothing(self):
        game1 = _postseason_event("E1", 2026, SEED_1, SEED_8, status="completed", home_score=110, away_score=100)
        game2 = _postseason_event("E2", 2026, SEED_1, SEED_8, status="scheduled")

        wins_a, wins_b = season_projection._series_record(SEED_1, SEED_8, [game1, game2])

        assert (wins_a, wins_b) == (1, 0)


class TestResolveSeriesMatchup:
    def test_no_real_games_falls_back_to_a_fresh_series_projection(self):
        result = season_projection._resolve_series_matchup(
            "a", "b", 1, 8, {}, MagicMock(), MagicMock(), MagicMock(), {"a": 1900, "b": 1400},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "projected"
        assert result["predicted_winner"] == "a"
        assert (result["wins_a"], result["wins_b"]) == (0, 0)
        # A predicted FINAL score, distinct from wins_a/wins_b's own
        # current (always 0-0 pre-series) record -- "a" (the favorite,
        # 1900 vs 1400 Elo) should be predicted to win the series outright.
        assert result["predicted_wins_a"] == 4
        assert result["predicted_wins_b"] < 4

    def test_a_series_decided_four_to_two_is_final_with_the_real_record(self):
        games = [
            _postseason_event(f"E{i}", 2026, SEED_1, SEED_8, status="completed", home_score=110, away_score=100)
            for i in range(4)
        ] + [
            _postseason_event(f"E{i}", 2026, SEED_8, SEED_1, status="completed", home_score=110, away_score=100)
            for i in range(4, 6)
        ]
        real_series = {frozenset((SEED_1, SEED_8)): games}

        result = season_projection._resolve_series_matchup(
            SEED_1, SEED_8, 1, 8, real_series, MagicMock(), MagicMock(), MagicMock(), {},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "final"
        assert result["actual_winner"] == SEED_1
        assert (result["wins_a"], result["wins_b"]) == (4, 2)
        assert result["predicted_winner"] is None  # decided -- nothing left to predict
        # A final series has nothing left to predict -- the real record
        # already answers the "how did it end" question.
        assert "predicted_wins_a" not in result
        assert "predicted_wins_b" not in result

    def test_an_in_progress_series_reports_the_live_record_and_a_series_probability(self):
        # 2-1 SEED_1, game 4 scheduled next.
        completed = [
            _postseason_event("E1", 2026, SEED_1, SEED_8, status="completed", home_score=110, away_score=100),
            _postseason_event("E2", 2026, SEED_1, SEED_8, status="completed", home_score=90, away_score=100),
            _postseason_event("E3", 2026, SEED_8, SEED_1, status="completed", home_score=95, away_score=105),
        ]
        next_game = _postseason_event("E4", 2026, SEED_8, SEED_1, status="scheduled")
        real_series = {frozenset((SEED_1, SEED_8)): completed + [next_game]}
        predictions_table = MagicMock()
        predictions_table.query.side_effect = [
            [],
            [{"model_key": "MODEL#win-probability#v1", "predicted_value": {"home_win_probability": 0.4}}],
        ]

        with patch.object(event_prediction, "compute_and_cache_event"):
            result = season_projection._resolve_series_matchup(
                SEED_1, SEED_8, 1, 8, real_series, MagicMock(), MagicMock(), predictions_table, {},
                season_simulation.DEFAULT_HOME_ADVANTAGE,
            )

        assert result["status"] == "scheduled"
        assert (result["wins_a"], result["wins_b"]) == (2, 1)
        # SEED_8 (home for game 4) at 40% -> SEED_1 (away) at 60% for that
        # game -- combined with its 2-1 series lead, SEED_1 should be a
        # strong series favorite, stronger than a bare 60%.
        assert result["predicted_winner"] == SEED_1
        assert result["win_probability"] > 0.6

    def test_a_series_with_completed_games_but_no_next_game_logged_falls_back_to_elo(self):
        # 2-2, next game not yet ingested/scheduled in our data.
        games = [
            _postseason_event("E1", 2026, SEED_1, SEED_8, status="completed", home_score=110, away_score=100),
            _postseason_event("E2", 2026, SEED_1, SEED_8, status="completed", home_score=90, away_score=100),
            _postseason_event("E3", 2026, SEED_8, SEED_1, status="completed", home_score=95, away_score=105),
            _postseason_event("E4", 2026, SEED_8, SEED_1, status="completed", home_score=105, away_score=95),
        ]
        real_series = {frozenset((SEED_1, SEED_8)): games}

        result = season_projection._resolve_series_matchup(
            SEED_1, SEED_8, 1, 8, real_series, MagicMock(), MagicMock(), MagicMock(), {SEED_1: 1900, SEED_8: 1400},
            season_simulation.DEFAULT_HOME_ADVANTAGE,
        )

        assert result["status"] == "scheduled"
        assert (result["wins_a"], result["wins_b"]) == (2, 2)
        assert result["predicted_winner"] == SEED_1  # the stronger Elo team


class TestBracketPayload:
    def _season_inputs(self, remaining_games=None):
        return {
            "current_season": 2026,
            "remaining_games": remaining_games or [],
            "wins": {}, "point_differential": {}, "current_ratings": {},
        }

    def test_returns_both_conferences_finals_and_a_champion(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []  # no real games yet -- fully projected
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, self._season_inputs(), {})

        assert set(result["conferences"]) == {"Eastern", "Western"}
        for rounds in result["conferences"].values():
            assert [r["round"] for r in rounds] == [
                "Play-In", "Play-In Elimination", "Conference Quarterfinals", "Conference Semifinals", "Conference Finals",
            ]
            assert len(rounds[0]["matchups"]) == 2  # play-in's games 1/2
            assert len(rounds[1]["matchups"]) == 1  # the elimination game, built from games 1/2's results
        assert result["champion"] == result["finals"]["predicted_winner"]

    def test_the_elimination_game_is_built_from_play_ins_own_two_games(self):
        # The elimination game's own participants are game 1's loser and
        # game 2's winner -- neither is a fresh team id, both trace back
        # to one of Play-In's own two games.
        storage = MagicMock()
        storage.get_all_events.return_value = []  # no real games yet -- fully projected
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, self._season_inputs(), {})

        for rounds in result["conferences"].values():
            play_in_games, elimination = rounds[0]["matchups"], rounds[1]["matchups"][0]
            play_in_teams = {t for m in play_in_games for t in (m["team_a"], m["team_b"])}
            assert elimination["team_a"] in play_in_teams
            assert elimination["team_b"] in play_in_teams

    def test_a_play_in_games_winner_reaches_its_own_quarterfinal_seed_pairing(self):
        # game 1's own winner (the seed-7 side) never plays in the
        # elimination game -- it advances straight to the Quarterfinals'
        # own (2 vs 7) pairing. Confirms the backend list order still
        # supports that (frontend connector-drawing is covered
        # separately, in season_page_test.dart).
        storage = MagicMock()
        storage.get_all_events.return_value = []  # no real games yet -- fully projected
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, self._season_inputs(), {})

        for rounds in result["conferences"].values():
            play_in_games, quarterfinals = rounds[0]["matchups"], rounds[2]["matchups"]
            game1 = next(m for m in play_in_games if m["seed_a"] == 7 or m["seed_b"] == 7)
            game1_winner = game1["predicted_winner"]
            assert any(game1_winner in (m["team_a"], m["team_b"]) for m in quarterfinals)

    def test_a_real_completed_play_in_game_is_reflected_in_the_bracket(self):
        # Seed 7 vs. seed 8 -- the play-in's own Game 1.
        real_game = _postseason_event("E1", 2026, SEED_7, SEED_8, status="completed", home_score=120, away_score=110, season_type=5)
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: [real_game] if status == "completed" else []
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, self._season_inputs(), {})

        play_in_matchups = result["conferences"]["Eastern"][0]["matchups"]
        real_matchup = next((m for m in play_in_matchups if {m["team_a"], m["team_b"]} == {SEED_7, SEED_8}), None)
        assert real_matchup is not None
        assert real_matchup["status"] == "final"
        assert real_matchup["actual_winner"] == SEED_7

    def test_seeds_from_projected_wins_when_the_regular_season_is_still_in_progress(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        simulation = {team_id: {"projected_wins": 60.0 if team_id == SEED_10 else 0.0} for team_id in season_simulation.TEAM_DIVISIONS}
        season_inputs = self._season_inputs(remaining_games=[(SEED_1, SEED_2)])  # non-empty -- still in progress

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, season_inputs, simulation)

        # SEED_10 (otherwise a bottom seed under all-zero real wins)
        # should now be seed 1 in the East -- verified indirectly: it
        # must have a bye out of the Play-In round (never appears there).
        play_in_teams = {t for m in result["conferences"]["Eastern"][0]["matchups"] for t in (m["team_a"], m["team_b"])}
        assert SEED_10 not in play_in_teams

    def test_a_real_first_round_series_in_progress_shows_the_live_record_not_one_game(self):
        # SEED_1 (2) vs SEED_8 (8) is Conference Quarterfinals' own (2, 8) pair --
        # season_type=3 (real playoff, not play-in's 5).
        real_games = [
            _postseason_event(f"F{i}", 2026, SEED_1, SEED_8, status="completed", home_score=110, away_score=100, season_type=3)
            for i in range(2)
        ] + [_postseason_event("F3", 2026, SEED_8, SEED_1, status="scheduled", season_type=3)]
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: (
            [g for g in real_games if g["status"] == status]
        )
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        result = season_projection._bracket_payload(storage, MagicMock(), predictions_table, self._season_inputs(), {})

        first_round = result["conferences"]["Eastern"][2]["matchups"]  # index 2 -- Play-In/Play-In Elimination come first
        series = next(m for m in first_round if {m["team_a"], m["team_b"]} == {SEED_1, SEED_8})
        assert series["status"] == "scheduled"
        assert (series["wins_a"], series["wins_b"]) == (2, 0)
        assert series["predicted_winner"] == SEED_1
