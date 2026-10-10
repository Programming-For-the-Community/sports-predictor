"""
Unit tests for library.features.nhl_dataset -- the chronological pass
that builds event and goalie rows: no row sees its own game or a later
one, franchise history survives a relocation, and exhibition games are
dropped. Uses real NHL team ids (BOS=1, NYR=13, LA=8, ARI=24, UTAH=129764).
"""
import logging

import pytest

from library.features import nhl, nhl_dataset

from _nhl_test_helpers import event as _event, goalie as _goalie, skater as _skater, team_box as _team_box

BOS, NYR, LA, ARIZONA, UTAH = "1", "13", "8", "24", "129764"
logger = logging.getLogger("test")


def _game(event_id, day, home, away, home_periods, away_periods, **kwargs):
    """(event, both team box rows, a starting goalie and one skater per side)."""
    game = _event(event_id, day, home, away, home_periods, away_periods, **kwargs)
    boxes = [_team_box(event_id, home), _team_box(event_id, away)]
    players = [
        _goalie(event_id, day, home, f"g-{home}"), _goalie(event_id, day, away, f"g-{away}"),
        _skater(event_id, day, home, f"s-{home}"), _skater(event_id, day, away, f"s-{away}"),
    ]
    return game, boxes, players


def _build_all(*games):
    events = [g[0] for g in games]
    boxes = [row for g in games for row in g[1]]
    players = [row for g in games for row in g[2]]
    return nhl_dataset.build_datasets(events, boxes, players, logger)


def _build(*games):
    """(event rows, goalie rows)."""
    return _build_all(*games)[:2]


def _skater_rows(*games):
    return _build_all(*games)[2]


class TestBuildDatasets:
    def test_one_event_row_per_game_in_date_order_and_two_goalie_rows(self):
        later = _game("2", "2026-01-12", BOS, NYR, [1, 0, 0], [0, 0, 0])
        earlier = _game("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])

        event_rows, goalie_rows = _build(later, earlier)

        assert [row["event_date"] for row in event_rows] == ["2026-01-10", "2026-01-12"]
        assert len(goalie_rows) == 4
        assert {row["entity_id"] for row in goalie_rows} == {f"g-{NYR}", f"g-{BOS}"}

    def test_first_game_has_no_history_and_the_second_sees_only_the_first(self):
        first = _game("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])
        second = _game("2", "2026-01-12", BOS, NYR, [1, 0, 0], [0, 0, 0])

        (row1, row2), goalie_rows = _build(first, second)

        assert row1["home_games_last10"] == 0
        assert row1["home_goals_for_last10"] is None
        assert row1["home_goalie_career_starts"] == 0
        # In game 2 the Rangers are away and carry game 1 (a 3-1 home win).
        assert row2["away_games_last10"] == 1
        assert (row2["away_goals_for_last10"], row2["away_goals_against_last10"]) == (3, 1)
        assert row2["home_goals_for_last10"] == 1
        assert row2["away_rest_days"] == 2
        assert row2["away_goalie_career_starts"] == 1
        assert row2["away_shots_for_last10"] == 30
        assert goalie_rows[2]["career_starts"] == 1

    def test_a_games_own_result_never_changes_its_own_row(self):
        history = _game("1", "2026-01-08", NYR, BOS, [2, 1, 0], [0, 1, 0])
        as_played = _game("2", "2026-01-10", NYR, BOS, [5, 0, 0], [0, 0, 0])
        reversed_result = _game("2", "2026-01-10", NYR, BOS, [0, 0, 0], [0, 0, 5])
        reversed_result[2][0]["stat_line"].update({"saves": 1, "shots_against": 40, "goals_against": 39})

        row_a = _build(history, as_played)[0][1]
        row_b = _build(history, reversed_result)[0][1]

        features_a = {k: v for k, v in row_a.items() if not k.startswith("label_")}
        features_b = {k: v for k, v in row_b.items() if not k.startswith("label_")}
        assert features_a == features_b
        assert row_a["label_home_won"] != row_b["label_home_won"]

    def test_a_later_game_never_changes_an_earlier_row(self):
        first = _game("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])
        second = _game("2", "2026-01-12", BOS, NYR, [1, 0, 0], [0, 0, 0])

        alone = _build(first)[0][0]
        with_later = _build(first, second)[0][0]

        assert alone == with_later

    def test_elo_uses_the_hockey_constants_and_only_earlier_results(self):
        first = _game("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])
        second = _game("2", "2026-01-12", NYR, BOS, [0, 0, 0], [1, 0, 0])

        row1, row2 = _build(first, second)[0]

        assert (row1["home_elo"], row1["away_elo"]) == (1500.0, 1500.0)
        assert row2["home_elo"] > 1500.0 > row2["away_elo"]
        assert row2["home_elo"] - 1500.0 < nhl.ELO_K_FACTOR

    def test_relocated_franchise_keeps_its_history_and_rating(self):
        as_arizona = _game("1", "2024-04-10", ARIZONA, LA, [3, 1, 0], [0, 0, 0], season=2024)
        as_utah = _game("2", "2024-04-12", UTAH, LA, [1, 0, 0], [0, 0, 0], season=2024)

        _, row = _build(as_arizona, as_utah)[0]

        assert row["home_entity_id"] == UTAH
        assert row["home_games_last10"] == 1
        assert row["home_goals_for_last10"] == 4
        assert row["home_elo"] > 1500.0

    def test_exhibition_games_are_dropped(self):
        all_star = _game("1", "2026-02-01", "129030", "129031", [3, 1, 0], [0, 0, 0])
        real = _game("2", "2026-02-05", NYR, BOS, [1, 0, 0], [0, 0, 0])

        event_rows, goalie_rows = _build(all_star, real)

        assert [row["home_entity_id"] for row in event_rows] == [NYR]
        assert len(goalie_rows) == 2

    def test_game_without_a_box_score_still_gets_an_event_row_but_no_goalie_rows(self):
        game = _event("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])

        event_rows, goalie_rows, skater_rows = nhl_dataset.build_datasets([game], [], [], logger)

        assert len(event_rows) == 1
        assert event_rows[0]["home_goalie_save_pct_career"] is None
        assert event_rows[0]["home_ice_time_share_missing"] is None
        assert goalie_rows == []
        assert skater_rows == []

    def test_goalie_row_carries_the_opponents_offence_and_his_own_stat_line_as_the_label(self):
        first = _game("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])
        second = _game("2", "2026-01-12", BOS, NYR, [1, 0, 0], [0, 0, 0])

        _, goalie_rows = _build(first, second)

        bruins_goalie = next(r for r in goalie_rows[2:] if r["team_id"] == BOS)
        assert (bruins_goalie["is_home"], bruins_goalie["opponent_id"]) == (1, NYR)
        assert bruins_goalie["opponent_goals_for_last10"] == 3     # the Rangers' three goals in game 1
        assert bruins_goalie["team_goals_against_last10"] == 3
        assert bruins_goalie["label_stat_line"]["saves"] == 27

    def test_lineup_gap_shows_up_once_a_regular_sits(self):
        games = []
        for i in range(1, 7):
            game = _game(str(i), f"2026-01-{2 * i:02d}", NYR, BOS, [1, 0, 0], [0, 0, 0])
            game[2].append(_skater(str(i), f"2026-01-{2 * i:02d}", NYR, "star", toi=1800, points=1))
            games.append(game)
        without_star = _game("7", "2026-01-14", NYR, BOS, [1, 0, 0], [0, 0, 0])

        row = _build(*games, without_star)[0][-1]

        assert row["home_ice_time_share_missing"] == pytest.approx(1800 / 2700)
        assert (row["home_regulars_missing"], row["home_top_scorers_out"]) == (1, 1)
        assert row["away_ice_time_share_missing"] == 0


class TestSkaterRows:
    def _games(self):
        first = _game("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0])
        first[2][2]["stat_line"].update({"shots_total": 4, "goals": 1, "points": 1, "hits": 2})
        first[2][2]["position"] = "C"
        second = _game("2", "2026-01-12", BOS, NYR, [1, 0, 0], [0, 0, 0])
        second[2][3]["stat_line"].update({"shots_total": 2, "goals": 0, "points": 0, "hits": 5})
        second[2][3]["position"] = "C"
        return first, second

    def test_one_row_per_skater_who_played_and_none_for_goalies(self):
        rows = _skater_rows(*self._games())

        assert len(rows) == 4
        assert {row["entity_id"] for row in rows} == {f"s-{NYR}", f"s-{BOS}"}

    def test_skater_row_sees_only_his_earlier_games_and_labels_this_one(self):
        rows = _skater_rows(*self._games())

        debut = next(r for r in rows if r["entity_id"] == f"s-{NYR}" and r["event_date"] == "2026-01-10")
        second = next(r for r in rows if r["entity_id"] == f"s-{NYR}" and r["event_date"] == "2026-01-12")
        assert debut["games_played"] == 0
        assert "avg_shots_total" not in debut
        assert (second["games_played"], second["avg_shots_total"], second["games_with_goals"]) == (1, 4, 1)
        assert second["shots_per_60"] == pytest.approx(4 / 900 * 3600)
        assert (second["is_center"], second["is_home"], second["opponent_id"]) == (1, 0, BOS)
        assert second["label_stat_line"]["hits"] == 5
        assert second["rest_days"] == 2

    def test_skater_row_carries_team_opponent_and_matchup_context(self):
        rows = _skater_rows(*self._games())

        second = next(r for r in rows if r["entity_id"] == f"s-{NYR}" and r["event_date"] == "2026-01-12")
        assert second["team_goals_for_last25"] == 3
        assert second["opponent_goals_against_last25"] == 3
        assert second["opponent_goalie_save_pct_season"] is not None
        # Boston's one earlier opponent (the Rangers) totalled four shots against them.
        assert second["opp_allowed_shots_total"] == 4
        assert second["share_shots_total"] == 1.0
        assert second["games_missed_last_10"] == 0

    def test_a_skaters_own_stat_line_never_changes_his_own_features(self):
        first, second = self._games()
        _, changed = self._games()
        changed[2][3]["stat_line"].update({"shots_total": 11, "goals": 4, "points": 4, "time_on_ice_seconds": 1500})

        def features(games):
            row = next(r for r in _skater_rows(*games) if r["entity_id"] == f"s-{NYR}" and r["event_date"] == "2026-01-12")
            return {k: v for k, v in row.items() if not k.startswith("label_")}

        assert features((first, second)) == features((first, changed))


class TestFranchiseEloRatings:
    def test_current_ratings_are_keyed_by_franchise(self):
        game, _, _ = _game("1", "2024-04-10", ARIZONA, LA, [3, 1, 0], [0, 0, 0], season=2024)

        _, current = nhl_dataset.franchise_elo_ratings([game])

        assert UTAH in current
        assert ARIZONA not in current
        assert current[UTAH] > 1500.0 > current[LA]
