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


FIELD = frozenset({"f1", "pga"})


def test_one_favourite_per_sport_replaces_the_last_and_every_sport_can_have_one(fake_table):
    table = fake_table()
    assert user_store.save_favorite(table, "s", "nfl", " det ", SPORTS, FIELD) == {"nfl": ["DET"]}
    assert user_store.save_favorite(table, "s", "nfl", "MIN", SPORTS, FIELD) == {"nfl": ["MIN"]}
    for sport, team in [("nba", "DEN"), ("ncaambb", "MINN"), ("ncaafb", "MINN"), ("nhl", "MIN"),
                        ("pga", "  Scottie   Scheffler "), ("f1", "Max Verstappen")]:
        user_store.save_favorite(table, "s", sport, team, SPORTS, FIELD)

    saved = user_store.get_favorites(table, "s")
    assert len(saved) == len(SPORTS)
    assert saved["pga"] == ["Scottie Scheffler"]
    assert saved["nfl"] == ["MIN"]


def test_a_field_sport_takes_a_name_and_a_team_sport_an_abbreviation(fake_table):
    table = fake_table()
    with pytest.raises(user_store.FavoriteError, match="name"):
        user_store.save_favorite(table, "s", "pga", "<script>", SPORTS, FIELD)
    with pytest.raises(user_store.FavoriteError, match="abbreviation"):
        user_store.save_favorite(table, "s", "nfl", "Scottie Scheffler", SPORTS, FIELD)
    assert user_store.save_favorite(table, "s", "f1", "Kimi Antonelli", SPORTS, FIELD) == {"f1": ["Kimi Antonelli"]}
    assert user_store.remove_favorite(table, "s", "f1", "kimi antonelli") == {}


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
