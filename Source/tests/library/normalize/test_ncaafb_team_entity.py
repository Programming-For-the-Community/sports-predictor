"""
Unit tests for library.normalize.ncaafb.team_to_entity -- id/name/
metadata mapping from CFBD's flat /teams entry shape. Hand-built
synthetic payloads.
"""
from library.normalize.ncaafb import team_to_entity


def _team(team_id="61", school="Georgia", **extra):
    return {
        "id": team_id,
        "school": school,
        "mascot": "Bulldogs",
        "abbreviation": "UGA",
        "conference": "SEC",
        "location": {"dome": False, "grass": True},
        **extra,
    }


class TestTeamToEntity:
    def test_entity_key_and_id_use_the_team_id(self):
        entity = team_to_entity(_team(team_id="61"), "ncaafb")
        assert entity["entity_id"] == "61"
        assert entity["entity_key"] == "SPORT#NCAAFB#ENTITY#61"

    def test_entity_type_is_team(self):
        entity = team_to_entity(_team(), "ncaafb")
        assert entity["entity_type"] == "team"

    def test_name_is_the_school_name(self):
        entity = team_to_entity(_team(school="Georgia"), "ncaafb")
        assert entity["name"] == "Georgia"

    def test_metadata_has_expected_fields(self):
        entity = team_to_entity(_team(), "ncaafb")
        assert entity["metadata"] == {
            "abbreviation": "UGA",
            "mascot": "Bulldogs",
            "conference": "SEC",
            "venue_indoor": False,
        }

    def test_venue_indoor_true_for_a_dome_team(self):
        entity = team_to_entity(_team(location={"dome": True}), "ncaafb")
        assert entity["metadata"]["venue_indoor"] is True

    def test_venue_indoor_none_when_location_missing(self):
        team = _team()
        del team["location"]
        entity = team_to_entity(team, "ncaafb")
        assert entity["metadata"]["venue_indoor"] is None

    def test_numeric_team_id_is_coerced_to_string(self):
        entity = team_to_entity(_team(team_id=61), "ncaafb")
        assert entity["entity_id"] == "61"
