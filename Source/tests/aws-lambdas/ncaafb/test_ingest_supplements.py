"""
Unit tests for aws-lambdas/ncaafb/ingest/supplements.py. The ingest
directory is on sys.path via conftest.py, as for enrichment.py.
"""
from unittest.mock import MagicMock

from supplements import save_supplements, tag_team_ids

TEAMS = [{"id": 61, "school": "Georgia"}, {"id": 333, "school": "Alabama"}]


def _client():
    client = MagicMock()
    client.get_advanced_game_stats.return_value = [{"gameId": 1, "team": "Georgia"}, {"gameId": 1, "team": "Nowhere State"}]
    client.get_talent.return_value = [{"team": "Alabama", "talent": 980.0}]
    client.get_returning_production.return_value = [{"team": "Georgia", "percentPPA": 0.6}]
    return client


def test_tag_team_ids_drops_unknown_schools():
    assert tag_team_ids([{"team": "Georgia"}, {"team": "Nowhere"}, "junk"], {"Georgia": "61"}) == [{"team": "Georgia", "team_id": "61"}]


def test_saves_the_weeks_advanced_stats_and_each_season_file_once():
    written = {}
    saved = save_supplements(
        _client(), 2025, "regular", 9, TEAMS, True,
        object_exists=lambda key: key == "ncaafb/talent/2025.json", put_json=written.__setitem__,
    )
    assert saved == {"advanced": True, "talent": False, "returning": True}
    assert written["ncaafb/advanced/2025/regular/9.json"] == [{"gameId": 1, "team": "Georgia", "team_id": "61"}]
    assert written["ncaafb/returning/2025.json"] == [{"team": "Georgia", "percentPPA": 0.6, "team_id": "61"}]
    assert "ncaafb/talent/2025.json" not in written


def test_no_advanced_stats_before_any_game_is_final():
    client = _client()
    save_supplements(client, 2025, "regular", 9, TEAMS, False, object_exists=lambda key: True, put_json=MagicMock())
    client.get_advanced_game_stats.assert_not_called()


def test_an_empty_season_listing_is_not_saved_so_the_next_run_retries():
    client = _client()
    client.get_talent.return_value = []
    put_json = MagicMock()
    saved = save_supplements(client, 2025, "regular", 9, TEAMS, False, object_exists=lambda key: False, put_json=put_json)
    assert saved["talent"] is False
    assert [c.args[0] for c in put_json.call_args_list] == ["ncaafb/returning/2025.json"]


def test_one_failure_does_not_stop_the_rest():
    client = _client()
    client.get_advanced_game_stats.side_effect = RuntimeError("CFBD down")
    put_json = MagicMock()
    saved = save_supplements(client, 2025, "regular", 9, TEAMS, True, object_exists=lambda key: False, put_json=put_json)
    assert saved == {"advanced": False, "talent": True, "returning": True}


def test_without_season_teams_nothing_is_fetched():
    client = _client()
    saved = save_supplements(client, 2025, "regular", 9, [], True, object_exists=lambda key: False, put_json=MagicMock())
    assert saved == {"advanced": False, "talent": False, "returning": False}
    client.get_talent.assert_not_called()
