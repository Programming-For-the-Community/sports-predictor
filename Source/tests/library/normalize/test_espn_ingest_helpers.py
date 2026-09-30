"""
Unit tests for library.normalize.espn.league_team_ids/attach_injuries --
the ingest-side helpers NBA/NCAA MBB share.
"""
from library.normalize.espn import attach_injuries, league_team_ids


def test_league_team_ids_lists_every_team_with_an_id():
    response = {"sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}, {"team": {}}, {"team": {"id": "2"}}]}]}]}

    assert league_team_ids(response) == ["1", "2"]


def test_league_team_ids_of_an_empty_response():
    assert league_team_ids({}) == []
    assert league_team_ids({"sports": [{"leagues": []}]}) == []


def test_attach_injuries_sets_only_the_sides_that_were_checked():
    events = [{"competitions": [{"competitors": [
        {"team": {"id": 1}, "homeAway": "home"},
        {"team": {"id": 2}, "homeAway": "away"},
        {"team": {"id": 3}, "homeAway": "neutral"},
    ]}]}, {}]

    attach_injuries(events, {"1": [{"entity_id": "p1"}], "3": []})

    assert events[0]["home_injuries"] == [{"entity_id": "p1"}]
    assert "away_injuries" not in events[0]
    assert events[1] == {}
