"""
Unit tests for migrations/fix_stale_player_team_ids.py. DynamoDBTable is
mocked throughout -- these verify the migration's own logic (deriving
each player's most-recent-game team_id, which entities actually need
patching, and that it never writes when there's nothing to do or
--dry-run is set), not real DynamoDB behavior.
"""
import os
from unittest.mock import MagicMock, patch

import fix_stale_player_team_ids as migration

os.environ.setdefault("PLAYER_GAME_STATS_TABLE_NAME", "test-player-game-stats")
os.environ.setdefault("ENTITIES_TABLE_NAME", "test-entities")


class TestMostRecentTeamByPlayer:
    def test_picks_the_latest_event_date_per_player(self):
        mock_table = MagicMock()
        mock_table.query.return_value = [
            {"entity_id": "walker3", "team_id": "12", "event_date": "2025-01-05"},
            {"entity_id": "walker3", "team_id": "26", "event_date": "2025-11-09"},
        ]

        result = migration._most_recent_team_by_player(mock_table, "nfl")

        assert result == {"walker3": ("26", "2025-11-09")}

    def test_skips_rows_missing_entity_id_or_team_id(self):
        mock_table = MagicMock()
        mock_table.query.return_value = [
            {"team_id": "12", "event_date": "2025-01-05"},
            {"entity_id": "walker3", "event_date": "2025-01-05"},
        ]

        result = migration._most_recent_team_by_player(mock_table, "nfl")

        assert result == {}


class TestMigrate:
    def test_patches_an_entity_whose_stored_team_id_is_stale(self):
        mock_stats_table = MagicMock()
        mock_stats_table.query.return_value = [
            {"entity_id": "walker3", "team_id": "26", "event_date": "2025-11-09"},
        ]
        mock_entities_table = MagicMock()
        mock_entities_table.get_item.return_value = {
            "entity_key": "SPORT#NFL#ENTITY#walker3",
            "metadata": {"team_id": "12", "team_id_as_of": "2024-09-01", "jersey": "9"},
        }

        with patch.object(migration, "DynamoDBTable", side_effect=[mock_stats_table, mock_entities_table]):
            migration.migrate("nfl", None, dry_run=False)

        written = mock_entities_table.put_item.call_args.args[0]
        assert written["metadata"]["team_id"] == "26"
        assert written["metadata"]["team_id_as_of"] == "2025-11-09"
        assert written["metadata"]["jersey"] == "9"  # untouched

    def test_skips_an_entity_already_correct(self):
        mock_stats_table = MagicMock()
        mock_stats_table.query.return_value = [
            {"entity_id": "walker3", "team_id": "26", "event_date": "2025-11-09"},
        ]
        mock_entities_table = MagicMock()
        mock_entities_table.get_item.return_value = {
            "entity_key": "SPORT#NFL#ENTITY#walker3",
            "metadata": {"team_id": "26", "team_id_as_of": "2025-11-09"},
        }

        with patch.object(migration, "DynamoDBTable", side_effect=[mock_stats_table, mock_entities_table]):
            migration.migrate("nfl", None, dry_run=False)

        mock_entities_table.put_item.assert_not_called()

    def test_skips_a_player_game_stats_row_with_no_matching_entity(self):
        mock_stats_table = MagicMock()
        mock_stats_table.query.return_value = [
            {"entity_id": "walker3", "team_id": "26", "event_date": "2025-11-09"},
        ]
        mock_entities_table = MagicMock()
        mock_entities_table.get_item.return_value = None

        with patch.object(migration, "DynamoDBTable", side_effect=[mock_stats_table, mock_entities_table]):
            migration.migrate("nfl", None, dry_run=False)  # must not raise

        mock_entities_table.put_item.assert_not_called()

    def test_dry_run_never_writes(self):
        mock_stats_table = MagicMock()
        mock_stats_table.query.return_value = [
            {"entity_id": "walker3", "team_id": "26", "event_date": "2025-11-09"},
        ]
        mock_entities_table = MagicMock()
        mock_entities_table.get_item.return_value = {
            "entity_key": "SPORT#NFL#ENTITY#walker3",
            "metadata": {"team_id": "12", "team_id_as_of": "2024-09-01"},
        }

        with patch.object(migration, "DynamoDBTable", side_effect=[mock_stats_table, mock_entities_table]):
            migration.migrate("nfl", None, dry_run=True)

        mock_entities_table.put_item.assert_not_called()


class TestMain:
    def test_default_sport_is_nfl(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch("sys.argv", ["fix_stale_player_team_ids.py"]):
            migration.main()

        assert mock_migrate.call_args.args[0] == "nfl"

    def test_dry_run_flag_is_threaded_through(self):
        with patch.object(migration, "migrate") as mock_migrate, \
             patch("sys.argv", ["fix_stale_player_team_ids.py", "--dry-run"]):
            migration.main()

        assert mock_migrate.call_args.args[2] is True
