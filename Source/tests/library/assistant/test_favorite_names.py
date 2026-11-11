"""A named favourite becomes the app's own team or competitor."""
import pytest

from library.assistant import favorite_names
from library.assistant.user_store import FavoriteError

FBS = [{"name": "Minnesota Golden Gophers", "abbreviation": "MINN"}, {"name": "Michigan Wolverines", "abbreviation": "MICH"},
       {"name": "Michigan State Spartans", "abbreviation": "MSU"}]


def _invoker(rows, key="standings", ok=True):
    def invoke(name, args):
        assert name == "get_season_projection"
        from library.assistant.game_list import names_team
        matched = [r for r in rows if names_team(args["team"], r.get("abbreviation"), r.get("name"))]
        return {"ok": ok, "result": {key: {"rows": matched}}}
    return invoke


def test_a_team_name_becomes_the_app_s_abbreviation_never_another_sport_s():
    assert favorite_names.resolve(_invoker(FBS), "ncaafb", "Minnesota Gophers", False) == "MINN"
    assert favorite_names.resolve(_invoker(FBS), "ncaafb", "minn", False) == "MINN"


def test_an_unknown_or_ambiguous_team_is_refused_with_the_choices():
    with pytest.raises(FavoriteError, match="No ncaafb team"):
        favorite_names.resolve(_invoker(FBS), "ncaafb", "MIN", False)
    with pytest.raises(FavoriteError, match="Michigan State Spartans"):
        favorite_names.resolve(_invoker(FBS), "ncaafb", "Michigan", False)


def test_a_field_sport_keeps_the_competitor_s_full_name():
    drivers = [{"name": "Andrea Kimi Antonelli"}, {"name": "Max Verstappen"}]
    assert favorite_names.resolve(_invoker(drivers, "driver_standings"), "f1", "Antonelli", True) == "Andrea Kimi Antonelli"


def test_without_standings_the_name_is_kept_for_the_store_to_check():
    assert favorite_names.resolve(_invoker([], ok=False), "nhl", "COL", False) == "COL"


def test_a_name_with_a_mascot_finds_a_team_listed_by_school_alone():
    fbs_short = [{"name": "Minnesota", "abbreviation": "MINN"}, {"name": "Miami", "abbreviation": "MIA"}]
    assert favorite_names.resolve(_invoker(fbs_short), "ncaafb", "Minnesota Gophers", False) == "MINN"
