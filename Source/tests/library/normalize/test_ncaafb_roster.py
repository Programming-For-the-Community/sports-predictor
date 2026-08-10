"""
Unit tests for library.normalize.ncaafb.roster_to_player_entities --
mapping from CFBD's /roster entry shape (NOT YET CONFIRMED LIVE, see that
function's own docstring) to player entity items. Hand-built synthetic
payloads matching CFBD's publicly documented v2 schema.
"""
from library.normalize.ncaafb import roster_to_player_entities


def _player(player_id="101", team_id="61", first="Carson", last="Beck", position="QB", **extra):
    return {
        "id": player_id,
        "teamId": team_id,
        "team": "Georgia",
        "firstName": first,
        "lastName": last,
        "position": position,
        "jersey": 15,
        **extra,
    }


class TestRosterToPlayerEntities:
    def test_entity_key_and_id_use_the_player_id(self):
        entities = roster_to_player_entities([_player(player_id="101")], "ncaafb", "2026-01-15")
        assert entities[0]["entity_id"] == "101"
        assert entities[0]["entity_key"] == "SPORT#NCAAFB#ENTITY#101"

    def test_entity_type_is_player(self):
        entities = roster_to_player_entities([_player()], "ncaafb", "2026-01-15")
        assert entities[0]["entity_type"] == "player"

    def test_name_joins_first_and_last(self):
        entities = roster_to_player_entities([_player(first="Carson", last="Beck")], "ncaafb", "2026-01-15")
        assert entities[0]["name"] == "Carson Beck"

    def test_team_key_uses_team_id(self):
        entities = roster_to_player_entities([_player(team_id="61")], "ncaafb", "2026-01-15")
        assert entities[0]["team_key"] == "SPORT#NCAAFB#TEAM#61"

    def test_metadata_has_expected_fields(self):
        entities = roster_to_player_entities([_player(team_id="61", position="QB")], "ncaafb", "2026-01-15")
        assert entities[0]["metadata"] == {
            "team_id": "61",
            "team_id_as_of": "2026-01-15",
            "jersey": 15,
            "position": "QB",
        }

    def test_numeric_ids_are_coerced_to_strings(self):
        entities = roster_to_player_entities([_player(player_id=101, team_id=61)], "ncaafb", "2026-01-15")
        assert entities[0]["entity_id"] == "101"
        assert entities[0]["metadata"]["team_id"] == "61"

    def test_skips_player_missing_id(self):
        player = _player()
        del player["id"]
        assert roster_to_player_entities([player], "ncaafb", "2026-01-15") == []

    def test_skips_player_missing_team_id(self):
        player = _player()
        del player["teamId"]
        assert roster_to_player_entities([player], "ncaafb", "2026-01-15") == []

    def test_name_falls_back_gracefully_when_last_name_missing(self):
        entities = roster_to_player_entities([_player(first="Carson", last=None)], "ncaafb", "2026-01-15")
        assert entities[0]["name"] == "Carson"

    def test_empty_roster_returns_empty_list(self):
        assert roster_to_player_entities([], "ncaafb", "2026-01-15") == []

    def test_multiple_players(self):
        entities = roster_to_player_entities(
            [_player(player_id="101"), _player(player_id="102", first="Trevor", last="Etienne", position="RB")],
            "ncaafb", "2026-01-15",
        )
        assert [e["entity_id"] for e in entities] == ["101", "102"]
        assert entities[1]["metadata"]["position"] == "RB"
