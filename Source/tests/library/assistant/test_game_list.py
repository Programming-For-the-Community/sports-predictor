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
