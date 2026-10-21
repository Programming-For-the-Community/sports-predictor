"""Unit tests for library.serving.common.betting_favorite."""
from library.serving.common import betting_favorite


def test_the_sportsbook_favorite_on_the_saved_line():
    assert betting_favorite({"pregame_odds": {"favorite": "away", "details": "OKC -1.5"}}) == "away"


def test_none_for_a_pickem_or_no_line():
    assert betting_favorite({"pregame_odds": {"favorite": None}}) is None
    assert betting_favorite({}) is None
