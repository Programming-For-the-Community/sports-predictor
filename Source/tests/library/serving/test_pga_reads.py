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
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = []

        result = pga_reads.list_events(storage, "pga", "scheduled")

        assert result == {"sport": "pga", "events": []}

    def test_scheduled_returns_every_matching_event_no_bucketing(self):
        """Unlike status=completed (see test_completed_bounds_to_the_most_
        recent_event_only below), scheduled isn't bounded -- there are
        only ever a handful of future tournaments in the registry at
        once, so every one of them comes back."""
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [
            _field_event("e1", "2026-08-20"), _field_event("e2", "2026-09-10"),
        ]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e1", "e2"}

    def test_completed_bounds_to_the_most_recent_event_only(self):
        # Real production 504 (2026-09-01): unbounded completed history
        # meant enriching up to ~150 golfers x every tournament ever
        # backfilled. Only the latest-dated event should come back.
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [
            _field_event("older", "2026-06-01", status="completed"),
            _field_event("newest", "2026-08-20", status="completed"),
            _field_event("middle", "2026-07-04", status="completed"),
        ]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "completed")

        assert [e["event_id"] for e in result["events"]] == ["newest"]

    def test_completed_queries_get_all_events_bounded_to_one(self):
        # Regression: most_recent_event's own post-hoc narrowing wasn't
        # enough on its own -- the unbounded get_all_events call itself
        # paginated through the sport's entire completed-event history
        # first, a real production 504 confirmed live 2026-09-02 (1186
        # PGA completed events) even after the entity-prefetch fix above.
        storage = MagicMock()
        storage.get_all_events.return_value = []

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_all_events.assert_called_once_with("pga", status="completed", limit=1)

    def test_completed_prefetches_entities_once_across_the_field_instead_of_per_participant(self):
        # The other half of the same fix -- even bounded to one
        # tournament, a ~150-golfer field enriches via one batched
        # prefetch, not 150 individual get_entity calls.
        storage = MagicMock()
        storage.get_entities.return_value = {}
        participants = [{"entity_id": "9478"}, {"entity_id": "3439"}]
        storage.get_all_events.return_value = [_field_event(participants=participants, status="completed")]
        storage.get_entity.return_value = None

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_entities.assert_called_once_with("pga", [("9478", "player"), ("3439", "player")])

    def test_end_date_is_carried_through(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [_field_event(end_date="2026-08-23")]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "scheduled")

        assert result["events"][0]["end_date"] == "2026-08-23"

    def test_event_type_is_carried_through(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [_cup_event()]
        storage.get_entity.return_value = None

        result = pga_reads.list_events(storage, "pga", "completed")

        assert result["events"][0]["event_type"] == "cup"


class TestEnrichPgaParticipants:
    def test_field_event_participants_are_looked_up_as_players(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [_field_event(participants=[{"entity_id": "9478"}])]
        storage.get_entity.return_value = {"name": "Scottie Scheffler", "metadata": {}}

        pga_reads.list_events(storage, "pga", "scheduled")

        storage.get_entity.assert_called_with("pga", "9478", "player")

    def test_cup_event_participants_are_looked_up_as_teams(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [_cup_event(participants=[{"entity_id": "1"}])]
        storage.get_entity.return_value = {"name": "USA", "metadata": {"abbreviation": "USA"}}

        pga_reads.list_events(storage, "pga", "completed")

        storage.get_entity.assert_called_with("pga", "1", "team")

    def test_team_match_play_participant_is_looked_up_as_a_team(self):
        """entity_id ("1") is the national team id, never present in this
        participant's own golfer_entity_ids -- a disjoint id space."""
        storage = MagicMock()
        storage.get_entities.return_value = {}
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
        storage.get_entities.return_value = {}
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
        storage.get_entities.return_value = {}
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


class TestGetSeasonProjection:
    def test_returns_the_cached_projection_from_its_own_key(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"sport": "pga", "season": 2026, "standings": []}

        result = pga_reads.get_season_projection(s3, "pga")

        assert result == {"sport": "pga", "season": 2026, "standings": []}
        s3.get_json.assert_called_once_with("season-projections/pga/latest.json")

    def test_returns_none_when_the_scheduled_job_hasnt_written_one_yet(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert pga_reads.get_season_projection(s3, "pga") is None
