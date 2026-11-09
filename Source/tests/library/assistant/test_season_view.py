"""The season projection shaped for the agent: ranked, filterable standings
and brackets flattened to readable matchup rows."""
from library.assistant import season_view

NAMES = {"1": {"name": "Buffalo Bills", "abbreviation": "BUF"}, "2": {"name": "Kansas City Chiefs", "abbreviation": "KC"},
         "3": {"name": "Detroit Lions", "abbreviation": "DET"}, "4": {"name": "Green Bay Packers", "abbreviation": "GB"}}


def _matchup(a, b, seed_a, seed_b, chance_a, pick, flag=None):
    return {"team_a": a, "team_b": b, "seed_a": seed_a, "seed_b": seed_b, "status": "projected",
            "chance_a": chance_a, "chance_b": 1 - chance_a, "predicted_winner": pick, "flag": flag}


PROJECTION = {
    "standings": [
        {"team_id": "2", "name": "Kansas City Chiefs", "abbreviation": "KC", "division": "AFC West", "projected_wins": 10.1, "color": "e31837"},
        {"team_id": "1", "name": "Buffalo Bills", "abbreviation": "BUF", "division": "AFC East", "projected_wins": 12.8},
        {"team_id": "3", "name": "Detroit Lions", "abbreviation": "DET", "division": "NFC North", "projected_wins": 11.0},
    ],
    "leaderboards": {"passing_yards": [{"entity_id": str(i), "name": f"QB {i}", "projected_total": 4000 - i} for i in range(8)]},
    "bracket": {
        "conferences": {"AFC": [{"round": "Divisional", "matchups": [_matchup("1", "2", 1, 4, 0.7, "1")]}],
                        "NFC": [{"round": "Divisional", "matchups": [_matchup("3", "4", 2, 3, 0.55, "4", "upset")]}]},
        "super_bowl": _matchup("1", "4", None, None, 0.6, "1"),
        "champion": "1",
        "round_odds": {"columns": ["Conference", "Title"], "groups": {
            "AFC": [{"team_id": "1", "seed": 1, "values": [0.5, 0.3]}, {"team_id": "2", "seed": 4, "values": [0.2, 0.1]}],
            "NFC": [{"team_id": "3", "seed": 2, "values": [0.4, 0.2]}]}},
        "simulations": 2000,
        "team_names": NAMES,
    },
}


def test_standings_rank_by_projection_drop_ids_and_cut_leaderboards():
    result = season_view.standings(PROJECTION, None, None, 100)

    assert [r["abbreviation"] for r in result["standings"]["rows"]] == ["BUF", "DET", "KC"]
    assert "team_id" not in result["standings"]["rows"][0]
    assert "color" not in result["standings"]["rows"][2]
    assert len(result["leaderboards"]["passing_yards"]) == season_view.LEADERBOARD_TOP


def test_standings_filter_by_team_or_division_and_say_when_cut():
    assert [r["abbreviation"] for r in season_view.standings(PROJECTION, "bills", None, 100)["standings"]["rows"]] == ["BUF"]
    assert season_view.standings(PROJECTION, None, "AFC", 100)["standings"]["count"] == 2
    cut = season_view.standings(PROJECTION, None, None, 1)["standings"]
    assert len(cut["rows"]) == 1
    assert "3" in cut["truncated"]


def test_brackets_become_matchup_rows_with_abbreviations_and_chances():
    result = season_view.brackets(PROJECTION, None, None, 100)["brackets"]["Playoffs"]

    assert result["champion"] == "BUF"
    rows = {(r["round"], r.get("group")): r for r in result["matchups"]}
    assert rows[("Divisional", "AFC")]["matchup"] == "BUF (1) 70% vs KC (4) 30%"
    assert rows[("Divisional", "NFC")]["pick"] == "GB"
    assert rows[("Divisional", "NFC")]["flag"] == "upset"
    assert rows[("Super Bowl", None)]["matchup"] == "BUF 60% vs GB 40%"
    assert [t["team"] for t in result["round_odds"]] == ["BUF", "DET", "KC"]  # by title odds
    assert result["round_odds"][0]["Title"] == 30.0


def test_a_team_filter_keeps_its_matchups_and_its_round_odds():
    result = season_view.brackets(PROJECTION, "Packers", None, 100)["brackets"]["Playoffs"]

    assert {r["round"] for r in result["matchups"]} == {"Divisional", "Super Bowl"}
    assert result["round_odds"] == []  # GB has no round_odds row in this projection
    assert season_view.brackets(PROJECTION, None, "NFC", 100)["brackets"]["Playoffs"]["count"] == 1


def test_conference_tournaments_are_listed_until_one_is_asked_for():
    projection = {"conference_brackets": [
        {"conference": "Big Ten", "bracket": {"rounds": [{"round": "Final", "matchups": [_matchup("3", "4", 1, 2, 0.6, "3")]}],
                                              "champion": "3", "team_names": NAMES}},
        {"conference": "SEC", "bracket": {"rounds": [], "champion": "2", "team_names": NAMES}},
    ]}

    overview = season_view.brackets(projection, None, None, 100)
    assert overview["brackets"] == {}
    assert overview["conference_tournaments"] == [{"conference": "Big Ten", "projected_champion": "DET"},
                                                  {"conference": "SEC", "projected_champion": "KC"}]
    big_ten = season_view.brackets(projection, None, "big ten", 100)["brackets"]
    assert list(big_ten) == ["Big Ten tournament"]
    assert big_ten["Big Ten tournament"]["matchups"][0]["pick"] == "DET"


def test_a_sport_without_brackets_says_so():
    assert season_view.brackets({"standings": []}, None, None, 100)["note"].startswith("This sport's projection has no bracket")
