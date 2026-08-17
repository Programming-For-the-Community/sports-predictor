"""
Unit tests for library.serving.common -- enrich_participants (shared by
nfl_reads.py and ncaafb_reads.py's own list_events), enrich_team_standings
(shared by every sport's season_projection.py), and
enrich_bracket_team_names (shared by every sport's own _bracket_payload,
added 2026-08-16 for the playoff-bracket feature). See their respective
test files for the through-list_events/through-build_season_projection
integration.
"""
from unittest.mock import MagicMock

from library.serving.common import enrich_bracket_team_names, enrich_participants, enrich_team_standings


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


class TestEnrichBracketTeamNames:
    def _storage(self):
        storage = MagicMock()
        storage.get_entity.side_effect = lambda sport, team_id: {
            "12": {"name": "Chiefs", "metadata": {"abbreviation": "KC"}},
            "24": {"name": "Chargers", "metadata": {"abbreviation": "LAC"}},
        }.get(team_id)
        return storage

    def test_collects_team_ids_from_conference_split_rounds(self):
        bracket = {
            "conferences": {
                "AFC": [{"round": "Wild Card", "matchups": [{"team_a": "12", "team_b": "24"}]}],
            },
        }

        result = enrich_bracket_team_names(self._storage(), "nfl", bracket)

        assert result["team_names"]["12"] == {"name": "Chiefs", "abbreviation": "KC", "color": None}
        assert result["team_names"]["24"] == {"name": "Chargers", "abbreviation": "LAC", "color": None}

    def test_collects_team_ids_from_a_flat_rounds_list(self):
        bracket = {"rounds": [{"round": "Round of 12", "matchups": [{"team_a": "12", "team_b": "24"}]}]}

        result = enrich_bracket_team_names(self._storage(), "ncaafb", bracket)

        assert set(result["team_names"]) == {"12", "24"}

    def test_collects_team_ids_from_the_final_matchup(self):
        bracket = {"conferences": {}, "super_bowl": {"team_a": "12", "team_b": "24"}}

        result = enrich_bracket_team_names(self._storage(), "nfl", bracket)

        assert set(result["team_names"]) == {"12", "24"}

    def test_a_team_id_appearing_in_multiple_matchups_is_only_looked_up_once(self):
        bracket = {
            "conferences": {
                "AFC": [
                    {"round": "Wild Card", "matchups": [{"team_a": "12", "team_b": "24"}]},
                    {"round": "Divisional", "matchups": [{"team_a": "12", "team_b": "99"}]},
                ],
            },
        }
        storage = self._storage()

        enrich_bracket_team_names(storage, "nfl", bracket)

        assert storage.get_entity.call_count == 3  # 12, 24, 99 -- not 4

    def test_missing_entity_degrades_to_none_fields_not_an_error(self):
        storage = MagicMock()
        storage.get_entity.return_value = None
        bracket = {"conferences": {}, "rounds": [{"round": "R1", "matchups": [{"team_a": "1", "team_b": "2"}]}]}

        result = enrich_bracket_team_names(storage, "ncaafb", bracket)

        assert result["team_names"]["1"] == {"name": None, "abbreviation": None, "color": None}

    def test_the_original_bracket_fields_are_preserved(self):
        bracket = {"conferences": {}, "champion": "12"}

        result = enrich_bracket_team_names(self._storage(), "nfl", bracket)

        assert result["champion"] == "12"
