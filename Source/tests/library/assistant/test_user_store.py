from datetime import date

import pytest

from library.assistant import user_store

SPORTS = ["f1", "nba", "ncaafb", "ncaambb", "nfl", "nhl", "pga"]


def test_the_daily_limit_stops_at_the_limit_per_user_and_agent(fake_table):
    table = fake_table()
    day = date(2026, 11, 6)
    results = [user_store.take_question(table, "sub-1", "public", 3, day) for _ in range(4)]

    assert results == [(True, 2), (True, 1), (True, 0), (False, 0)]
    assert user_store.take_question(table, "sub-1", "admin", 3, day) == (True, 2)
    assert user_store.take_question(table, "sub-2", "public", 3, day) == (True, 2)
    assert user_store.questions_left(table, "sub-1", "public", 3, day) == 0
    assert user_store.take_question(table, "sub-1", "public", 3, date(2026, 11, 7)) == (True, 2)


def test_favorites_are_normalised_deduplicated_and_limited(fake_table):
    table = fake_table()
    assert user_store.save_favorite(table, "s", "nfl", " det ", SPORTS) == {"nfl": ["DET"]}
    assert user_store.save_favorite(table, "s", "nfl", "DET", SPORTS) == {"nfl": ["DET"]}
    user_store.save_favorite(table, "s", "nfl", "GB", SPORTS)
    with pytest.raises(user_store.FavoriteError, match="per sport"):
        user_store.save_favorite(table, "s", "nfl", "CHI", SPORTS)
    user_store.save_favorite(table, "s", "nba", "DEN", SPORTS)
    user_store.save_favorite(table, "s", "nhl", "COL", SPORTS)
    with pytest.raises(user_store.FavoriteError, match="sports"):
        user_store.save_favorite(table, "s", "pga", "ABC", SPORTS)


@pytest.mark.parametrize("sport, team", [("cricket", "IND"), ("nfl", "Detroit Lions"), ("nfl", "<b>")])
def test_bad_favorites_are_refused(sport, team, fake_table):
    table = fake_table()
    with pytest.raises(user_store.FavoriteError):
        user_store.save_favorite(table, "s", sport, team, SPORTS)


def test_favorites_can_be_removed_one_by_one_or_cleared(fake_table):
    table = fake_table()
    user_store.save_favorite(table, "s", "nfl", "DET", SPORTS)
    user_store.save_favorite(table, "s", "nba", "DEN", SPORTS)

    assert user_store.remove_favorite(table, "s", "nfl", "det") == {"nba": ["DEN"]}
    assert user_store.clear_favorites(table, "s", "nba") == {}
    user_store.save_favorite(table, "s", "nfl", "DET", SPORTS)
    assert user_store.clear_favorites(table, "s") == {}
    assert user_store.get_favorites(table, "s") == {}
