"""
Unit tests for library.serving.f1_reads.
"""
from unittest.mock import MagicMock

from library.serving import f1_reads


def _field_event(event_id="2026-5", event_date="2026-05-24", status="scheduled", participants=None):
    return {
        "event_id": event_id, "event_key": f"K{event_id}", "event_type": "field", "event_date": event_date,
        "status": status, "season": 2026, "week": 5, "race_name": "Monaco Grand Prix", "circuit_id": "monaco",
        "participants": participants if participants is not None else [{"entity_id": "max_verstappen"}],
        "venue_name": "Circuit de Monaco", "venue_city": "Monte Carlo", "venue_state": "Monaco",
    }


class TestListEvents:
    def test_empty_when_nothing_stored(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = []

        result = f1_reads.list_events(storage, "f1", "scheduled")

        assert result == {"sport": "f1", "events": []}

    def test_scheduled_returns_every_matching_event(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [_field_event("e1"), _field_event("e2")]
        storage.get_entity.return_value = None

        result = f1_reads.list_events(storage, "f1", "scheduled")

        assert {e["event_id"] for e in result["events"]} == {"e1", "e2"}

    def test_completed_bounds_to_the_most_recent_event_only(self):
        # Same fix as pga_reads.py's own list_events -- real complaint
        # 2026-09-01: unbounded completed history across every race ever
        # backfilled, same architectural gap as PGA's own 504.
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [
            _field_event("older", "2026-03-02", status="completed"),
            _field_event("newest", "2026-05-24", status="completed"),
            _field_event("middle", "2026-04-06", status="completed"),
        ]
        storage.get_entity.return_value = None

        result = f1_reads.list_events(storage, "f1", "completed")

        assert [e["event_id"] for e in result["events"]] == ["newest"]

    def test_enriches_participants_as_players(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        storage.get_all_events.return_value = [_field_event(participants=[{"entity_id": "max_verstappen"}])]
        storage.get_entity.return_value = {"name": "Max Verstappen", "metadata": {}}

        result = f1_reads.list_events(storage, "f1", "scheduled")

        assert result["events"][0]["participants"][0]["name"] == "Max Verstappen"
        storage.get_entity.assert_called_with("f1", "max_verstappen", "player")

    def test_prefetches_entities_once_across_the_whole_field(self):
        storage = MagicMock()
        storage.get_entities.return_value = {}
        participants = [{"entity_id": "max_verstappen"}, {"entity_id": "lando_norris"}]
        storage.get_all_events.return_value = [_field_event(participants=participants, status="completed")]
        storage.get_entity.return_value = None

        f1_reads.list_events(storage, "f1", "completed")

        storage.get_entities.assert_called_once_with("f1", [("max_verstappen", "player"), ("lando_norris", "player")])


class TestModelVersionsFor:
    def test_field_includes_constructor_model(self):
        versions = f1_reads.model_versions_for("field")
        assert "constructor_win_probability" in versions
        assert versions["win_probability"] == "win-probability"

    def test_sprint_has_its_own_model_set(self):
        versions = f1_reads.model_versions_for("sprint")
        assert versions["win_probability"] == "sprint-win-probability"
        assert "constructor_win_probability" not in versions


class TestResultFingerprint:
    def test_zero_for_a_scheduled_event_with_no_results_yet(self):
        event = {"participants": [{"entity_id": "a", "result": {}}]}
        assert f1_reads.result_fingerprint(event) == 0

    def test_increments_once_qualifying_lands(self):
        event = {"participants": [{"entity_id": "a", "result": {"qualifying": {"position": 1}}}]}
        assert f1_reads.result_fingerprint(event) == 1

    def test_increments_again_once_the_race_completes(self):
        event = {"participants": [{"entity_id": "a", "result": {"status": "finished", "qualifying": {"position": 1}}}]}
        assert f1_reads.result_fingerprint(event) == 2

    def test_sums_across_every_participant(self):
        event = {"participants": [
            {"entity_id": "a", "result": {"status": "finished"}},
            {"entity_id": "b", "result": {"status": "dnf"}},
        ]}
        assert f1_reads.result_fingerprint(event) == 2


class TestGetSeasonProjection:
    def test_none_when_not_yet_written(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert f1_reads.get_season_projection(s3, "f1") is None

    def test_reads_back_the_cached_projection(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"sport": "f1", "season": 2026}

        assert f1_reads.get_season_projection(s3, "f1") == {"sport": "f1", "season": 2026}
