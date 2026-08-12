"""
Unit tests for library.serving.common -- enrich_participants (shared by
nfl_reads.py and ncaafb_reads.py's own list_events) and
enrich_team_standings (shared by both sports' season_projection.py). See
their respective test files for the through-list_events/
through-build_season_projection integration.
"""
from unittest.mock import MagicMock

from library.serving.common import enrich_participants, enrich_team_standings


class TestEnrichParticipants:
    def test_none_passes_through_unchanged(self):
        assert enrich_participants(MagicMock(), "nfl", None) is None

    def test_empty_list_passes_through_unchanged(self):
        assert enrich_participants(MagicMock(), "nfl", []) == []

    def test_attaches_name_abbreviation_and_conference_from_the_entity(self):
        storage = MagicMock()
        storage.get_entity.return_value = {
            "name": "Alabama", "metadata": {"abbreviation": "ALA", "conference": "SEC"},
        }

        result = enrich_participants(storage, "ncaafb", [{"entity_id": "333", "role": "home"}])

        assert result[0]["name"] == "Alabama"
        assert result[0]["abbreviation"] == "ALA"
        assert result[0]["conference"] == "SEC"
        assert result[0]["entity_id"] == "333"
        assert result[0]["role"] == "home"
        storage.get_entity.assert_called_once_with("ncaafb", "333")

    def test_missing_entity_degrades_to_none_fields_not_an_error(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        result = enrich_participants(storage, "ncaafb", [{"entity_id": "333", "role": "away"}])

        assert result[0]["name"] is None
        assert result[0]["abbreviation"] is None
        assert result[0]["conference"] is None

    def test_entity_with_no_metadata_degrades_to_none_abbreviation(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Alabama"}

        result = enrich_participants(storage, "ncaafb", [{"entity_id": "333", "role": "home"}])

        assert result[0]["name"] == "Alabama"
        assert result[0]["abbreviation"] is None

    def test_each_participant_resolved_independently(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, entity_id: {
            "12": {"name": "Chiefs", "metadata": {"abbreviation": "KC"}},
            "24": {"name": "Chargers", "metadata": {"abbreviation": "LAC"}},
        }[entity_id]

        result = enrich_participants(storage, "nfl", [
            {"entity_id": "12", "role": "home"}, {"entity_id": "24", "role": "away"},
        ])

        assert [p["abbreviation"] for p in result] == ["KC", "LAC"]


class TestEnrichTeamStandings:
    def test_attaches_name_and_abbreviation_from_the_entity(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Alabama", "metadata": {"abbreviation": "ALA"}}

        result = enrich_team_standings(storage, "ncaafb", [{"team_id": "333", "wins": 5}])

        assert result[0]["name"] == "Alabama"
        assert result[0]["abbreviation"] == "ALA"
        assert result[0]["team_id"] == "333"
        assert result[0]["wins"] == 5
        storage.get_entity.assert_called_once_with("ncaafb", "333")

    def test_missing_entity_degrades_to_none_fields_not_an_error(self):
        storage = MagicMock()
        storage.get_entity.return_value = None

        result = enrich_team_standings(storage, "ncaafb", [{"team_id": "333"}])

        assert result[0]["name"] is None
        assert result[0]["abbreviation"] is None

    def test_each_row_resolved_independently(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, team_id: {
            "12": {"name": "Chiefs", "metadata": {"abbreviation": "KC"}},
            "24": {"name": "Chargers", "metadata": {"abbreviation": "LAC"}},
        }[team_id]

        result = enrich_team_standings(storage, "nfl", [{"team_id": "12"}, {"team_id": "24"}])

        assert [row["abbreviation"] for row in result] == ["KC", "LAC"]

    def test_empty_list_passes_through_unchanged(self):
        assert enrich_team_standings(MagicMock(), "nfl", []) == []
