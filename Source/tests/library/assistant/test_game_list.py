"""Game-list totals, filters, the app's UPSET rule and the live join."""
import pytest

from library.assistant import game_list


def _game(event_id="1", status="scheduled", home="DET", away="GB", favorite="home", moneyline=-250, home_p=0.55,
          comparison=None, conferences=("NFC North", "NFC North")):
    game = {
        "event_id": event_id, "status": status, "betting_favorite": favorite, "betting_favorite_moneyline": moneyline,
        "participants": [
            {"role": "home", "abbreviation": home, "name": f"{home} Team", "conference": conferences[0]},
            {"role": "away", "abbreviation": away, "name": f"{away} Team", "conference": conferences[1]},
        ],
        "prediction": {"predictions": {"win_probability": {"home_win_probability": home_p}}},
    }
    if comparison is not None:
        game["prediction_comparison"] = comparison
    return game


@pytest.mark.parametrize("game, flag", [
    (_game(home_p=0.55), "UPSET WATCH"),                       # favourite at 55% <= 60%
    (_game(home_p=0.7), None),                                 # model agrees with the book
    (_game(home_p=0.4, moneyline=-150), None),                 # favourite not short enough to count
    (_game(favorite="away", home_p=0.45), "UPSET WATCH"),      # away favourite at 55%
    (_game(status="completed", comparison={"actual_home_won": False, "correct": False}), "UPSET"),
    (_game(status="completed", comparison={"actual_home_won": True, "correct": True}), None),
    (_game(favorite=None), None),
])
def test_upset_flags_follow_the_app_rule(game, flag):
    assert game_list.upset_flag(game) == flag


def test_totals_cover_every_game_and_grade_the_finished_ones():
    games = [_game(str(i), "completed", comparison={"actual_home_won": True, "correct": i % 4 != 0}) for i in range(40)]

    totals = game_list.totals(games)

    assert totals["games"] == 40
    assert (totals["graded"], totals["correct"], totals["accuracy_pct"]) == (40, 30, 75.0)


def test_team_and_conference_filters():
    games = [_game("1", home="DET", away="GB"), _game("2", home="OSU", away="MICH", conferences=("Big Ten", "Big Ten")),
             _game("3", home="DUKE", away="UNC", conferences=("ACC", "ACC"))]

    assert [g["event_id"] for g in game_list.filtered(games, "det", None)] == ["1"]
    assert [g["event_id"] for g in game_list.filtered(games, "MICH Team", None)] == ["2"]
    assert [g["event_id"] for g in game_list.filtered(games, None, "big ten")] == ["2"]
    assert game_list.conferences_seen(games) == ["ACC", "Big Ten", "NFC North"]


def test_live_games_join_scores_to_the_day_s_games():
    games = [_game("1"), _game("2"), _game("3")]
    live = {"events": {"1": {"live": True, "detail": "2nd 8:41", "home_score": 33, "away_score": 16},
                       "2": {"live": False, "completed": False}, "3": {"live": False, "completed": True, "home_score": 70, "away_score": 60}}}

    joined = game_list.live_games(games, live)

    assert [g["event_id"] for g in joined] == ["1", "3"]
    assert joined[0]["live"] == {"live": True, "detail": "2nd 8:41", "home_score": 33, "away_score": 16}


def test_day_totals_break_down_by_conference_counting_a_conference_game_once():
    def game(home_conf, away_conf, correct):
        return {"status": "completed", "prediction_comparison": {"correct": correct},
                "participants": [{"role": "home", "conference": home_conf}, {"role": "away", "conference": away_conf}]}
    events = [game("Big Ten", "Big Ten", True), game("Big Ten", "SEC", False), game("WCC", None, True),
              {"status": "completed", "participants": [{"conference": "SEC"}]}]  # no grade: left out

    rows = game_list.totals(events)["by_conference"]

    assert rows == [
        {"conference": "Big Ten", "graded": 2, "correct": 1, "accuracy_pct": 50.0},
        {"conference": "SEC", "graded": 1, "correct": 0, "accuracy_pct": 0.0},
        {"conference": "WCC", "graded": 1, "correct": 1, "accuracy_pct": 100.0},
    ]
    assert "by_conference" not in game_list.totals([{"status": "completed", "participants": []}])


def _player_game(passer, team, predicted, actual):
    return {"status": "completed", "participants": [{"role": "home", "abbreviation": team}, {"role": "away", "abbreviation": "OPP"}],
            "leaders_comparison": {
                "home": {"passing": {"name": passer, "predicted": {"passing_yards": predicted}, "actual": {"passing_yards": actual}}},
                "away": {"rushing": [{"name": "Back", "predicted": {"rushing_yards": 80.0}, "actual": {}}]}}}


def test_player_totals_sum_every_game_s_predicted_vs_actual_per_stat():
    rows = game_list.player_totals([_player_game("A", "GB", 250.0, 300), _player_game("B", "DET", 220.0, 200),
                                    {"status": "completed", "leaders_comparison": None}])

    assert rows == [{"stat": "passing_yards", "graded": 2, "avg_miss": 35.0, "avg_lean": -15.0,
                     "biggest_misses": [{"player": "A", "team": "GB", "predicted": 250.0, "actual": 300},
                                        {"player": "B", "team": "DET", "predicted": 220.0, "actual": 200}]}]


def test_one_stat_lists_every_graded_player_with_the_team_from_the_game():
    games = [_player_game(f"QB{i}", f"T{i}", 200.0 + i, 200) for i in range(5)]

    rows = game_list.player_totals(games, "passing_yards", max_rows=3)

    assert [r["stat"] for r in rows] == ["passing_yards"]
    assert rows[0]["graded"] == 5
    assert rows[0]["players"][0] == {"player": "QB4", "team": "T4", "predicted": 204.0, "actual": 200, "miss": 4.0}
    assert len(rows[0]["players"]) == 3
