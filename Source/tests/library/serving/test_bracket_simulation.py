"""
Unit tests for library.serving.bracket_simulation: a four-team, two-round
bracket (1v4, 2v3, then the winners) over fake real-game events.
"""
import math

import pytest

from library.normalize.odds import no_vig_home_chance
from library.serving.bracket_simulation import (
    BracketSimulation,
    MatchupRules,
    RealPostseason,
    measured_home_edge,
    series_probability,
    series_win_chance,
)

# team_a's chance in every game, by (team_a, team_b).
STRENGTH = {"A": 0.8, "B": 0.6, "C": 0.4, "D": 0.2}
COLUMNS = [("Final", "Final"), ("Title", None)]


def _game(key, home, away, *, status="scheduled", home_score=None, away_score=None, favorite=None, kickoff="2026-04-20T00:00Z"):
    event = {
        "event_key": key, "event_id": key, "status": status, "kickoff_time": kickoff,
        "participants": [
            {"entity_id": home, "role": "home", "result": {"score": home_score, "won": (home_score or 0) > (away_score or 0)}},
            {"entity_id": away, "role": "away", "result": {"score": away_score, "won": (away_score or 0) > (home_score or 0)}},
        ],
    }
    if favorite is not None:
        event["pregame_odds"] = {"favorite": favorite}
    return event


def _rules(wins_needed=1):
    def rules(round_name, team_a, team_b, home_advantage):
        p = STRENGTH[team_a] / (STRENGTH[team_a] + STRENGTH[team_b])
        return MatchupRules(wins_needed=wins_needed, game_probability=lambda i: p)
    return rules


def _topology(play_round):
    semis, advancing = play_round("Semifinal", [("A", "D", 1, 4), ("B", "C", 2, 3)])
    (w1, s1), (w2, s2) = advancing
    final, champion = play_round("Final", [(w1, w2, s1, s2)])
    return {"rounds": [semis, final], "champion": champion[0][0]}


def _simulate(events=(), wins_needed=1, logged=None, simulations=400, tiebreak=lambda a, b: None):
    real = RealPostseason(list(events), logged or (lambda event: None))
    engine = BracketSimulation(real, _rules(wins_needed), tiebreak=tiebreak, simulations=simulations)
    return engine.run(_topology, COLUMNS, group_of=lambda team: "League")


class TestSeriesProbability:
    def test_an_even_game_is_an_even_series(self):
        assert series_probability(lambda i: 0.5, 4) == pytest.approx(0.5)

    def test_best_of_seven_at_sixty_percent_a_game(self):
        assert series_probability(lambda i: 0.6, 4) == pytest.approx(0.710208, abs=1e-6)

    def test_starts_from_the_record(self):
        assert series_probability(lambda i: 0.5, 4, 3, 0) == pytest.approx(15 / 16)
        assert series_probability(lambda i: 0.9, 4, 0, 4) == 0.0


class TestDisplayedBracket:
    def test_the_same_real_results_give_the_same_bracket(self):
        first = _simulate()
        second = _simulate()
        assert first == second

    def test_every_slot_names_a_winner_from_the_chosen_run(self):
        bracket = _simulate()
        final = bracket["rounds"][1]["matchups"][0]
        semis = bracket["rounds"][0]["matchups"]
        assert {final["team_a"], final["team_b"]} == {semis[0]["predicted_winner"], semis[1]["predicted_winner"]}
        assert bracket["champion"] == final["predicted_winner"]
        for row in semis + [final]:
            assert row["status"] == "projected"
            assert row["chance_a"] + row["chance_b"] == pytest.approx(1)

    def test_a_decided_real_game_keeps_its_real_winner_everywhere(self):
        # D beat A in the real semifinal.
        bracket = _simulate([_game("g1", "A", "D", status="completed", home_score=90, away_score=95)])
        semi = bracket["rounds"][0]["matchups"][0]
        assert semi["status"] == "final"
        assert semi["actual_winner"] == "D"
        assert (semi["actual_home_score"], semi["actual_away_score"]) == (90, 95)
        assert "D" in (bracket["rounds"][1]["matchups"][0]["team_a"], bracket["rounds"][1]["matchups"][0]["team_b"])
        odds = {row["team_id"]: row["values"] for row in bracket["round_odds"]["groups"]["League"]}
        assert odds["A"] == [0, 0]
        assert odds["D"][0] == 1

    def test_a_real_scheduled_game_uses_its_logged_prediction(self):
        game = _game("g1", "A", "D")
        bracket = _simulate([game], logged=lambda event: 0.3)
        semi = bracket["rounds"][0]["matchups"][0]
        assert semi["status"] == "scheduled"
        assert semi["chance_a"] == pytest.approx(0.3)


class TestSeries:
    def test_a_series_under_way_finishes_from_its_real_record(self):
        events = [
            _game(f"g{i}", "B", "C", status="completed", home_score=80, away_score=90, kickoff=f"2026-04-2{i}T00:00Z")
            for i in range(3)
        ]
        bracket = _simulate(events, wins_needed=4)
        semi = bracket["rounds"][0]["matchups"][1]
        assert (semi["wins_a"], semi["wins_b"]) == (0, 3)
        assert semi["series_wins_b"] >= 3
        assert max(semi["series_wins_a"], semi["series_wins_b"]) == 4
        assert semi["status"] == "scheduled"

    def test_a_decided_series_shows_its_real_record(self):
        events = [
            _game(f"g{i}", "A", "D", status="completed", home_score=100, away_score=90, kickoff=f"2026-04-2{i}T00:00Z")
            for i in range(4)
        ]
        semi = _simulate(events, wins_needed=4)["rounds"][0]["matchups"][0]
        assert semi["status"] == "final"
        assert (semi["series_wins_a"], semi["series_wins_b"]) == (4, 0)
        assert "predicted_wins_a" not in semi


class TestFavoriteAndFlags:
    def test_the_better_seed_is_favoured_without_a_line(self):
        assert _simulate()["rounds"][0]["matchups"][0]["favorite"] == "A"

    def test_the_line_beats_the_seeds(self):
        bracket = _simulate([_game("g1", "A", "D", favorite="away")])
        assert bracket["rounds"][0]["matchups"][0]["favorite"] == "D"

    def test_the_tiebreak_settles_matching_seeds(self):
        def topology(play_round):
            round_payload, advancing = play_round("Final", [("B", "C", None, None)])
            return {"rounds": [round_payload], "champion": advancing[0][0]}

        real = RealPostseason([], lambda event: None)
        engine = BracketSimulation(real, _rules(), tiebreak=lambda a, b: "C", simulations=50)
        row = engine.run(topology, COLUMNS, group_of=lambda team: "League")["rounds"][0]["matchups"][0]
        assert row["favorite"] == "C"

    def test_a_real_upset_is_flagged(self):
        # D, the line's underdog, beat A.
        bracket = _simulate([_game("g1", "A", "D", status="completed", home_score=90, away_score=95, favorite="home")])
        assert bracket["rounds"][0]["matchups"][0]["flag"] == "upset"

    def test_a_close_favourite_win_is_an_upset_watch(self):
        # B over C: the model gives B 60% (0.6 / (0.6 + 0.4)).
        bracket = _simulate([_game("g1", "B", "C", status="completed", home_score=95, away_score=90)])
        assert bracket["rounds"][0]["matchups"][1]["flag"] == "watch"

    def test_a_comfortable_favourite_win_has_no_flag(self):
        # A over D: the model gives A 80%.
        bracket = _simulate([_game("g1", "A", "D", status="completed", home_score=95, away_score=90)])
        assert bracket["rounds"][0]["matchups"][0]["flag"] is None


class TestRepresentativeRun:
    def test_the_shown_bracket_has_the_typical_number_of_upsets(self):
        bracket = _simulate(simulations=2000)
        shown = sum(row["flag"] == "upset" for r in bracket["rounds"] for row in r["matchups"])
        # Expected upsets here: P(D beats A) + P(C beats B) + P(final upset) ~ 0.2 + 0.4 + ~0.3.
        assert shown == 1


class TestRoundOdds:
    def test_title_odds_sum_to_one_and_never_exceed_reaching_the_final(self):
        odds = _simulate(simulations=2000)["round_odds"]
        assert odds["columns"] == ["Final", "Title"]
        rows = odds["groups"]["League"]
        assert sum(row["values"][1] for row in rows) == pytest.approx(1)
        for row in rows:
            assert row["values"][1] <= row["values"][0]

    def test_rows_are_ordered_by_seed(self):
        rows = _simulate()["round_odds"]["groups"]["League"]
        assert [row["team_id"] for row in rows] == ["A", "B", "C", "D"]


class TestByes:
    def test_a_bye_advances_its_team_without_a_flag(self):
        def topology(play_round):
            first, advancing = play_round("Round 1", [("A", None, 1, None), ("B", "C", 2, 3)])
            final, champion = play_round("Final", [(advancing[0][0], advancing[1][0], advancing[0][1], advancing[1][1])])
            return {"rounds": [first, final], "champion": champion[0][0]}

        real = RealPostseason([], lambda event: None)
        bracket = BracketSimulation(real, _rules(), simulations=50).run(topology, COLUMNS, group_of=lambda t: "L")
        bye = bracket["rounds"][0]["matchups"][0]
        assert bye["predicted_winner"] == "A"
        assert "flag" not in bye
        assert bracket["rounds"][1]["matchups"][0]["team_a"] == "A"


class TestRealPostseason:
    def test_counts_series_wins_by_team_not_home_or_away(self):
        real = RealPostseason([
            _game("g1", "A", "D", status="completed", home_score=100, away_score=90, kickoff="2026-04-20T00:00Z"),
            _game("g2", "D", "A", status="completed", home_score=100, away_score=90, kickoff="2026-04-22T00:00Z"),
            _game("g3", "D", "A", status="completed", home_score=80, away_score=90, kickoff="2026-04-24T00:00Z"),
            _game("g4", "A", "D", kickoff="2026-04-26T00:00Z"),
        ], lambda event: None)
        assert real.record("A", "D") == (2, 1)
        assert real.next_game("A", "D")["event_key"] == "g4"

    def test_games_missing_a_side_are_left_out(self):
        broken = {"event_key": "x", "status": "completed", "participants": []}
        real = RealPostseason([broken, _game("g1", "A", "D", status="completed", home_score=1, away_score=0)], lambda event: None)
        assert real.record("A", "D") == (1, 0)


NBA_HOME_EDGE = 0.32  # logit(0.58), about the NBA's home win share


def _line(home_ml, away_ml, locked=True):
    line = {"favorite": "home" if home_ml < away_ml else "away", "home_moneyline": home_ml, "away_moneyline": away_ml}
    return {**line, "locked_at": "2026-04-19T23:45:00+00:00"} if locked else line


def _series_slot(events, home_edge=NBA_HOME_EDGE):
    def topology(play_round):
        round_payload, advancing = play_round("Final", [("A", "D", 1, 4)])
        return {"rounds": [round_payload], "champion": advancing[0][0]}

    real = RealPostseason(list(events), lambda event: None)
    engine = BracketSimulation(real, _rules(wins_needed=4), simulations=50, home_edge=home_edge)
    return engine.run(topology, COLUMNS, group_of=lambda team: "League")["rounds"][0]["matchups"][0]


def _game_one(line, status="scheduled"):
    event = _game("g1", "A", "D", status=status, home_score=100 if status == "completed" else None, away_score=90 if status == "completed" else None)
    event["pregame_odds"] = line
    return event


class TestSeriesWinChance:
    def test_a_modest_game_one_favourite_is_the_series_underdog(self):
        # -130/+110: the host is 54% in Game 1 but only ~45% over the series.
        host = no_vig_home_chance(_line(-130, 110))
        assert host == pytest.approx(0.543, abs=1e-3)
        assert series_win_chance(host, NBA_HOME_EDGE, 4) == pytest.approx(0.445, abs=1e-3)

    def test_a_clear_game_one_favourite_stays_the_series_favourite(self):
        assert series_win_chance(no_vig_home_chance(_line(-180, 150)), NBA_HOME_EDGE, 4) > 0.5

    def test_with_no_home_edge_the_game_one_chance_carries_over(self):
        assert series_win_chance(0.6, 0.0, 4) == pytest.approx(series_probability(lambda i: 0.6, 4))


class TestMeasuredHomeEdge:
    def _results(self, home_wins, away_wins):
        return [
            _game(f"h{i}", "A", "D", status="completed", home_score=100, away_score=90) for i in range(home_wins)
        ] + [
            _game(f"a{i}", "A", "D", status="completed", home_score=90, away_score=100) for i in range(away_wins)
        ]

    def test_logit_of_the_home_win_share(self):
        assert measured_home_edge(self._results(174, 126)) == pytest.approx(math.log(174 / 126))

    def test_too_few_games_measure_no_edge(self):
        assert measured_home_edge(self._results(10, 5)) == 0.0


class TestSeriesFavorite:
    def test_comes_from_the_locked_game_one_line_with_home_court_removed(self):
        # A hosts and is the Game 1 favourite, but D is likelier to win the series.
        row = _series_slot([_game_one(_line(-130, 110))])
        assert row["favorite"] == "D"

    def test_the_better_seed_until_game_one_locks(self):
        row = _series_slot([_game_one(_line(-115, -105, locked=False))])
        assert row["favorite"] == "A"

    def test_a_started_game_one_uses_its_line_even_without_a_lock(self):
        row = _series_slot([_game_one(_line(-130, 110, locked=False), status="completed")])
        assert row["favorite"] == "D"

    def test_a_line_without_moneylines_falls_back_to_the_seeds(self):
        row = _series_slot([_game_one({"favorite": "away", "locked_at": "t"})])
        assert row["favorite"] == "A"

    def test_a_single_game_still_uses_its_own_line(self):
        bracket = _simulate([_game("g1", "A", "D", favorite="away")])
        assert bracket["rounds"][0]["matchups"][0]["favorite"] == "D"
