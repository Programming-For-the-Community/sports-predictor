"""Unit tests for library.serving.common.betting_favorite and betting_favorite_moneyline."""
from library.serving.common import betting_favorite, betting_favorite_moneyline


def test_the_sportsbook_favorite_on_the_saved_line():
    assert betting_favorite({"pregame_odds": {"favorite": "away", "details": "OKC -1.5"}}) == "away"


def test_none_for_a_pickem_or_no_line():
    assert betting_favorite({"pregame_odds": {"favorite": None}}) is None
    assert betting_favorite({}) is None


def test_the_favorites_own_moneyline():
    line = {"favorite": "away", "home_moneyline": 180, "away_moneyline": -220}
    assert betting_favorite_moneyline({"pregame_odds": line}) == -220


def test_no_moneyline_without_a_favorite_or_its_odds():
    assert betting_favorite_moneyline({"pregame_odds": {"favorite": None, "home_moneyline": -110}}) is None
    assert betting_favorite_moneyline({"pregame_odds": {"favorite": "home", "home_moneyline": None}}) is None
    assert betting_favorite_moneyline({}) is None
