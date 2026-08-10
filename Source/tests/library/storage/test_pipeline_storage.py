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
from boto3.dynamodb.conditions import Or

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


class TestGetEntity:
    def test_reads_by_entity_key(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.get_item.return_value = {"entity_id": "61", "name": "Georgia"}

        result = storage.get_entity("ncaafb", "61")

        mock_entities.get_item.assert_called_once_with({"entity_key": "SPORT#NCAAFB#ENTITY#61"})
        assert result == {"entity_id": "61", "name": "Georgia"}

    def test_returns_none_when_missing(self, storage_env):
        storage, mock_entities = _make_storage(storage_env)
        mock_entities.get_item.return_value = None

        assert storage.get_entity("ncaafb", "999") is None
