"""
Unit tests for migrations/migrate_entity_key_types.py. DynamoDBTable is
mocked throughout -- these verify the migration's own logic (which rows
get moved, onto what new key, and that old-key rows are cleaned up), not
real DynamoDB behavior.
"""
import os
from unittest.mock import MagicMock, patch

import migrate_entity_key_types as migration

os.environ.setdefault("ENTITIES_TABLE_NAME", "test-entities")


class TestMigrate:
    def test_moves_a_team_row_onto_the_type_scoped_key(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NBA#ENTITY#25", "entity_id": "25", "sport": "nba", "entity_type": "team", "name": "OKC"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.put_item.assert_called_once_with({
            "entity_key": "SPORT#NBA#ENTITY#TEAM#25", "entity_id": "25", "sport": "nba",
            "entity_type": "team", "name": "OKC",
        })
        mock_table.delete_item.assert_called_once_with({"entity_key": "SPORT#NBA#ENTITY#25"})

    def test_moves_a_player_row_onto_the_type_scoped_key(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NBA#ENTITY#25", "entity_id": "25", "sport": "nba", "entity_type": "player", "name": "Metta World Peace"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.put_item.assert_called_once_with({
            "entity_key": "SPORT#NBA#ENTITY#PLAYER#25", "entity_id": "25", "sport": "nba",
            "entity_type": "player", "name": "Metta World Peace",
        })
        mock_table.delete_item.assert_called_once_with({"entity_key": "SPORT#NBA#ENTITY#25"})

    def test_skips_rows_already_on_the_new_key_format(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NBA#ENTITY#TEAM#26", "entity_id": "26", "sport": "nba", "entity_type": "team"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.put_item.assert_not_called()
        mock_table.delete_item.assert_not_called()

    def test_skips_a_row_missing_entity_type_rather_than_guessing(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NBA#ENTITY#25", "entity_id": "25", "sport": "nba"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.put_item.assert_not_called()
        mock_table.delete_item.assert_not_called()

    def test_dry_run_never_writes_or_deletes(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = [
            {"entity_key": "SPORT#NBA#ENTITY#25", "entity_id": "25", "sport": "nba", "entity_type": "team"},
        ]

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=True)

        mock_table.put_item.assert_not_called()
        mock_table.delete_item.assert_not_called()

    def test_does_nothing_when_no_old_format_rows_exist(self):
        mock_table = MagicMock()
        mock_table.scan.return_value = []

        with patch.object(migration, "DynamoDBTable", return_value=mock_table):
            migration.migrate(None, dry_run=False)

        mock_table.put_item.assert_not_called()
        mock_table.delete_item.assert_not_called()


class TestMain:
    def test_dry_run_flag_is_threaded_through(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch("sys.argv", ["migrate_entity_key_types.py", "--dry-run"]):
            migration.main()

        assert mock_migrate.call_args.args[1] is True

    def test_default_is_not_a_dry_run(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch("sys.argv", ["migrate_entity_key_types.py"]):
            migration.main()

        assert mock_migrate.call_args.args[1] is False
