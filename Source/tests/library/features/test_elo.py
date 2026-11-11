"""Unit tests for library.features.elo and compute_elo_ratings' neutral-site and margin-formula handling."""
import logging
import math

from library.features import elo
from library.features.common import MOV_LOG, MOV_POWER, _mov_multiplier, compute_elo_ratings


def _event(key, date, home_score, away_score, season=2024, neutral=None, home="H", away="A"):
    event = {
        "event_key": key, "event_date": date, "season": season,
        "participants": [
            {"entity_id": home, "role": "home", "result": {"score": home_score}},
            {"entity_id": away, "role": "away", "result": {"score": away_score}},
        ],
    }
    if neutral is not None:
        event["neutral_site"] = neutral
    return event


class TestNeutralSite:
    def test_a_neutral_site_home_win_moves_ratings_like_an_even_game(self):
        _, home_court = compute_elo_ratings([_event("E1", "2024-01-01", 70, 60)], home_advantage=100.0)
        _, neutral = compute_elo_ratings([_event("E1", "2024-01-01", 70, 60, neutral=True)], home_advantage=100.0)
        # Without the home edge the win was less expected, so it earns more.
        assert neutral["H"] > home_court["H"]
        _, even = compute_elo_ratings([_event("E1", "2024-01-01", 70, 60)], home_advantage=0.0)
        assert math.isclose(neutral["H"], even["H"])


class TestMarginFormulas:
    def test_power_formula_is_basketballs(self):
        assert math.isclose(_mov_multiplier(10, 0.0, 7.5, 0.006, MOV_POWER), 13 ** 0.8 / 7.5)

    def test_log_formula_is_the_default(self):
        assert math.isclose(_mov_multiplier(10, 0.0, 2.2, 0.001), math.log(11))
        assert _mov_multiplier(10, 0.0, 2.2, 0.001, MOV_LOG) == _mov_multiplier(10, 0.0, 2.2, 0.001)

    def test_compute_elo_ratings_uses_the_given_formula(self):
        events = [_event("E1", "2024-01-01", 120, 90)]
        _, log_ratings = compute_elo_ratings(events, mov_formula=MOV_LOG)
        _, power_ratings = compute_elo_ratings(events, mov_formula=MOV_POWER, mov_base=7.5, mov_divisor=0.006)
        assert log_ratings["H"] != power_ratings["H"]


class TestSportElo:
    def test_basketball_uses_the_power_formula_and_a_bigger_home_edge(self):
        nba, nfl = elo.params_for("nba"), elo.params_for("nfl")
        assert nba.mov_formula == MOV_POWER and nfl.mov_formula == MOV_LOG
        assert nba.home_advantage > nfl.home_advantage

    def test_sport_elo_ratings_matches_compute_elo_ratings_with_the_sports_constants(self):
        events = [_event("E1", "2024-01-01", 100, 90), _event("E2", "2024-01-03", 95, 99)]
        params = elo.params_for("ncaambb")
        expected = compute_elo_ratings(
            events, k_factor=params.k_factor, home_advantage=params.home_advantage, season_carryover=params.season_carryover,
            mov_formula=params.mov_formula, mov_base=params.mov_base, mov_divisor=params.mov_divisor, as_of_season=2025,
        )
        assert elo.sport_elo_ratings("ncaambb", events, as_of_season=2025) == expected


class TestSweep:
    EVENTS = [
        _event(f"E{i}", f"{2023 + i // 20}-01-{1 + i % 20:02d}", 100 + (i % 3) * 5, 95, season=2023 + i // 20)
        for i in range(60)
    ]

    def test_log_loss_skips_the_first_season(self):
        first_season_only = [e for e in self.EVENTS if e["season"] == 2023]
        assert elo.elo_log_loss(first_season_only, elo.params_for("nba")) is None
        assert elo.elo_log_loss(self.EVENTS, elo.params_for("nba")) > 0

    def test_sweep_is_ordered_best_first(self):
        results = elo.sweep("nba", self.EVENTS, {"k_factor": (10.0, 40.0), "home_advantage": (0.0, 200.0)})
        assert len(results) == 4
        assert [loss for loss, _ in results] == sorted(loss for loss, _ in results)
        # Home teams win every game here, so the biggest home edge fits best.
        assert results[0][1].home_advantage == 200.0

    def test_log_sweep_logs_the_current_constants_and_the_best(self, caplog):
        with caplog.at_level(logging.INFO):
            elo.log_sweep("nfl", self.EVENTS, logging.getLogger("test"), top=2)
        messages = [r.getMessage() for r in caplog.records]
        assert messages[0].startswith("Elo sweep (nfl): current constants log loss")
        assert len(messages) == 3
