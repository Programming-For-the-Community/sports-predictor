"""
Unit tests for PipelineStorage.upsert_player_entity -- the
metadata.team_id_as_of guard that stops an out-of-order box score write
from clobbering a player's team_id with a stale one (see that method's
own docstring). DynamoDBTable/S3Manager are mocked -- these verify the
condition expression and item passed to put_item, not real DynamoDB
behavior (see tests/library/aws/test_dynamodb_table.py for that).
"""
from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.conditions import Key, Or

from library.storage.pipeline_storage import PipelineStorage


@pytest.fixture
def storage_env(monkeypatch):
    monkeypatch.setenv("RAW_BUCKET_NAME", "test-raw-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("ENTITIES_TABLE_NAME", "test-entities")
    monkeypatch.setenv("EVENTS_TABLE_NAME", "test-events")
    monkeypatch.setenv("PLAYER_GAME_STATS_TABLE_NAME", "test-player-game-stats")
    monkeypatch.setenv("TEAM_GAME_STATS_TABLE_NAME", "test-team-game-stats")


def _make_storage(storage_env):
    mock_entities = MagicMock()
    with patch("library.storage.pipeline_storage.S3Manager"), \
         patch("library.storage.pipeline_storage.DynamoDBTable") as mock_table_cls:
        mock_table_cls.side_effect = [mock_entities, MagicMock(), MagicMock(), MagicMock()]
        storage = PipelineStorage()
    return storage, mock_entities


def _make_storage_with_events(storage_env):
    mock_events = MagicMock()
    with patch("library.storage.pipeline_storage.S3Manager"), \
         patch("library.storage.pipeline_storage.DynamoDBTable") as mock_table_cls:
        mock_table_cls.side_effect = [MagicMock(), mock_events, MagicMock(), MagicMock()]
        storage = PipelineStorage()
    return storage, mock_events


class TestGetEvent:
    def test_reads_one_event_by_its_own_key(self, storage_env):
        storage, mock_events = _make_storage_with_events(storage_env)
        mock_events.get_item.return_value = {"event_key": "SPORT#F1#EVENT#2024-1", "status": "completed"}

        result = storage.get_event("SPORT#F1#EVENT#2024-1")

        assert result == {"event_key": "SPORT#F1#EVENT#2024-1", "status": "completed"}
        mock_events.get_item.assert_called_once_with({"event_key": "SPORT#F1#EVENT#2024-1"})

    def test_returns_none_when_no_such_event_exists(self, storage_env):
        storage, mock_events = _make_storage_with_events(storage_env)
        mock_events.get_item.return_value = None

        assert storage.get_event("SPORT#F1#EVENT#missing") is None


class TestUpsertPlayerEntity:
    def test_writes_with_a_condition_expression(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        entity = {"entity_id": "walker3", "metadata": {"team_id": "26", "team_id_as_of": "2025-11-09"}}

        storage.upsert_player_entity(entity)

        mock_entities.put_item.assert_called_once()
        call = mock_entities.put_item.call_args
        assert call.args[0] == entity
        assert call.kwargs["condition_expression"] is not None

    def test_condition_is_not_exists_or_stored_as_of_lte_new_value(self, storage_env):
        # Exercises the actual boto3.dynamodb.conditions expression rather
        # than just asserting one was passed -- confirms it encodes "no
        # stored value yet" OR "stored value is same-or-older" (comparing
        # ISO date strings, which sort lexicographically), not some other
        # relation that would silently let a stale write through.
        storage, mock_entities = _make_storage(storage_env)
        entity = {"entity_id": "walker3", "metadata": {"team_id": "26", "team_id_as_of": "2025-11-09"}}

        storage.upsert_player_entity(entity)

        condition = mock_entities.put_item.call_args.kwargs["condition_expression"]
        assert isinstance(condition, Or)
        not_exists_clause, lte_clause = condition.get_expression()["values"]
        assert not_exists_clause.get_expression()["operator"] == "attribute_not_exists"
        assert lte_clause.get_expression()["operator"] == "<="
        assert lte_clause.get_expression()["values"][1] == "2025-11-09"


class TestUpsertPlayerEntityReturnValue:
    def test_returns_true_when_put_item_writes(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.put_item.return_value = True
        entity = {"entity_id": "walker3", "metadata": {"team_id": "26", "team_id_as_of": "2025-11-09"}}

        assert storage.upsert_player_entity(entity) is True

    def test_returns_false_when_put_item_rejects_the_condition(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.put_item.return_value = False
        entity = {"entity_id": "walker3", "metadata": {"team_id": "26", "team_id_as_of": "2025-11-09"}}

        assert storage.upsert_player_entity(entity) is False


class TestGetEventsByStatus:
    def test_queries_the_sport_status_index(self, storage_env):
        storage, mock_events = _make_storage_with_events(storage_env)
        mock_events.query.return_value = []

        storage.get_events_by_status("ncaafb", "scheduled")

        call = mock_events.query.call_args
        assert call.args[0] == Key("sport_status").eq("ncaafb#scheduled")
        assert call.kwargs["index_name"] == "sport-status-index"

    def test_returns_whatever_the_index_query_returns(self, storage_env):
        storage, mock_events = _make_storage_with_events(storage_env)
        mock_events.query.return_value = [{"event_id": "1", "sport": "ncaafb"}]

        result = storage.get_events_by_status("ncaafb", "scheduled")

        assert [e["event_id"] for e in result] == ["1"]


class TestUpsertEvent:
    def test_derives_sport_status_from_sport_and_status(self, storage_env):
        storage, mock_events = _make_storage_with_events(storage_env)
        event = {"event_key": "SPORT#NCAAFB#EVENT#1", "sport": "ncaafb", "status": "scheduled"}

        storage.upsert_event(event)

        written = mock_events.put_item.call_args.args[0]
        assert written["sport_status"] == "ncaafb#scheduled"
        # The caller's own dict is left untouched -- upsert_event writes a
        # copy, not a mutated version of what it was handed.
        assert "sport_status" not in event

    def test_leaves_sport_status_off_when_sport_or_status_is_missing(self, storage_env):
        storage, mock_events = _make_storage_with_events(storage_env)

        storage.upsert_event({"event_key": "SPORT#NCAAFB#EVENT#1", "status": "scheduled"})

        written = mock_events.put_item.call_args.args[0]
        assert "sport_status" not in written


class TestGetEntity:
    def test_reads_by_entity_key(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.get_item.return_value = {"entity_id": "61", "name": "Georgia"}

        result = storage.get_entity("ncaafb", "61", "team")

        mock_entities.get_item.assert_called_once_with({"entity_key": "SPORT#NCAAFB#ENTITY#TEAM#61"})
        assert result == {"entity_id": "61", "name": "Georgia"}

    def test_player_and_team_types_read_different_keys(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.get_item.return_value = None

        storage.get_entity("nba", "25", "player")

        mock_entities.get_item.assert_called_once_with({"entity_key": "SPORT#NBA#ENTITY#PLAYER#25"})

    def test_returns_none_when_missing(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.get_item.return_value = None

        assert storage.get_entity("ncaafb", "999", "team") is None
