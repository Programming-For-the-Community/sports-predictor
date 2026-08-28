"""
Unit tests for library.serving.pga_reads.
"""
from unittest.mock import MagicMock

from library.serving import pga_reads


def _field_event(event_id="401811963", event_date="2026-08-20", end_date="2026-08-23", status="scheduled", participants=None):
    return {
        "event_id": event_id, "event_key": f"K{event_id}", "event_type": "field", "event_date": event_date,
        "end_date": end_date,
        "status": status, "season": {"year": 2026}, "tournament_name": "BMW Championship",
        "participants": participants if participants is not None else [{"entity_id": "9478"}],
        "venue_name": "Bellerive", "venue_city": "St. Louis", "venue_state": "MO",
    }


def _cup_event(event_id="401465497", participants=None):
    return {
        "event_id": event_id, "event_key": f"K{event_id}", "event_type": "cup", "event_date": "2022-09-22",
        "status": "completed", "season": {"year": 2023}, "tournament_name": "Presidents Cup",
        "participants": participants if participants is not None else [{"entity_id": "1"}, {"entity_id": "3"}],
        "venue_name": "Quail Hollow", "venue_city": "Charlotte", "venue_state": "NC",
    }


def _match_play_event(event_id="401465497-match-10951", participants=None):
    return {
        "event_id": event_id, "event_key": f"K{event_id}", "event_type": "match_play", "event_date": "2022-09-22",
        "status": "completed", "season": {"year": 2023}, "tournament_name": "Presidents Cup",
        "participants": participants if participants is not None else [
            {"entity_id": "1", "golfer_entity_ids": ["1085", "1086"]},  # team match play
            {"entity_id": "3", "golfer_entity_ids": ["2001", "2002"]},
        ],
        "venue_name": "Quail Hollow", "venue_city": "Charlotte", "venue_state": "NC",
    }


class TestListEvents:
    def test_empty_when_nothing_stored(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []

        result = pga_reads.list_events(storage, "pga", "scheduled")

        assert result == {"sport": "pga", "events": []}

    def test_returns_every_matching_event_no_date_bucketing(self):
        """Unlike nba_reads.list_events, PGA's list_events doesn't narrow
        to a single soonest/most-recent date -- every event at the
        requested status comes back."""
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _field_event("e1", "2026-08-20"), _field_event("e2", "2026-09-10"),
        ]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e1", "e2"}

    def test_end_date_is_carried_through(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event(end_date="2026-08-23")]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "scheduled")

        assert result["events"][0]["end_date"] == "2026-08-23"

    def test_event_type_is_carried_through(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_cup_event()]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "completed")

        assert result["events"][0]["event_type"] == "cup"


class TestEnrichPgaParticipants:
    def test_field_event_participants_are_looked_up_as_players(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_field_event(participants=[{"entity_id": "9478"}])]
        storage.get_entity.return_value = {"name": "Scottie Scheffler", "metadata": {}}

        pga_reads.list_events(storage, "pga", "scheduled")

        storage.get_entity.assert_called_with("pga", "9478", "player")

    def test_cup_event_participants_are_looked_up_as_teams(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_cup_event(participants=[{"entity_id": "1"}])]
        storage.get_entity.return_value = {"name": "USA", "metadata": {"abbreviation": "USA"}}

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_entity.assert_called_with("pga", "1", "team")

    def test_team_match_play_participant_is_looked_up_as_a_team(self):
        """entity_id ("1") is the national team id, never present in this
        participant's own golfer_entity_ids -- a disjoint id space."""
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_play_event(participants=[{"entity_id": "1", "golfer_entity_ids": ["1085", "1086"]}]),
        ]
        storage.get_entity.return_value = {"name": "USA", "metadata": {}}

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_entity.assert_called_with("pga", "1", "team")

    def test_individual_wgc_match_play_participant_is_looked_up_as_a_player(self):
        """entity_id doubles as this golfer's own single-element
        golfer_entity_ids -- WGC has no team layer at all."""
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_play_event(participants=[{"entity_id": "3439", "golfer_entity_ids": ["3439"]}]),
        ]
        storage.get_entity.return_value = {"name": "Scottie Scheffler", "metadata": {}}

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_entity.assert_called_with("pga", "3439", "player")

    def test_match_play_event_can_mix_team_and_individual_lookups_per_participant(self):
        """Not a realistic single-event mix (a real match_play event is
        consistently team-shaped or consistently individual-shaped), but
        proves per-participant resolution doesn't just look at the first
        participant and apply that type to the whole list."""
        storage = MagicMock()
        storage.get_all_events.return_value = [
            _match_play_event(participants=[
                {"entity_id": "1", "golfer_entity_ids": ["1085", "1086"]},  # team
                {"entity_id": "3439", "golfer_entity_ids": ["3439"]},  # individual
            ]),
        ]
        storage.get_entity.return_value = {"name": "x", "metadata": {}}

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_entity.assert_any_call("pga", "1", "team")
        storage.get_entity.assert_any_call("pga", "3439", "player")


class TestModelVersionsFor:
    def test_field_maps_to_the_full_golfer_level_model_set(self):
        assert pga_reads.model_versions_for("field") == pga_reads.FIELD_EVENT_MODEL_VERSIONS

    def test_match_play_maps_to_the_match_winprob_model(self):
        assert pga_reads.model_versions_for("match_play") == {"match_win_probability": "match-win-probability"}

    def test_cup_maps_to_the_cup_winprob_model(self):
        assert pga_reads.model_versions_for("cup") == {"cup_win_probability": "cup-win-probability"}
