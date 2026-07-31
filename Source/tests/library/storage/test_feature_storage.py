"""
Unit tests for FeatureStorage's read methods. DynamoDBTable itself is
mocked -- these verify FeatureStorage builds the right query/scan calls
and filters/sorts scan results correctly, not DynamoDB behavior (see
tests/library/aws/test_dynamodb_table.py for that).
"""
from unittest.mock import MagicMock, patch

import pytest

from library.storage.feature_storage import FeatureStorage


@pytest.fixture
def storage_env(monkeypatch):
    monkeypatch.setenv("EVENTS_TABLE_NAME", "test-events")
    monkeypatch.setenv("PLAYER_GAME_STATS_TABLE_NAME", "test-player-game-stats")


def _make_storage(storage_env):
    mock_events = MagicMock()
    mock_stats = MagicMock()
    with patch("library.storage.feature_storage.DynamoDBTable") as mock_table_cls:
        mock_table_cls.side_effect = [mock_events, mock_stats]
        storage = FeatureStorage()
    return storage, mock_events, mock_stats


def _event(event_id, entity_id, sport="nfl", status="completed", event_date="2025-09-01"):
    return {
        "event_id": event_id,
        "sport": sport,
        "status": status,
        "event_date": event_date,
        "participants": [{"entity_id": entity_id}],
    }


class TestGetPlayerGameStats:
    def test_queries_entity_history_index_most_recent_first(self, storage_env):
        storage, _, mock_stats = _make_storage(storage_env)
        mock_stats.query.return_value = [{"event_date": "2025-09-28"}]

        result = storage.get_player_game_stats("mahomes-patrick")

        assert result == [{"event_date": "2025-09-28"}]
        call_kwargs = mock_stats.query.call_args.kwargs
        assert call_kwargs["index_name"] == "entity-history"
        assert call_kwargs["scan_index_forward"] is False
        assert call_kwargs["limit"] is None

    def test_passes_limit_through(self, storage_env):
        storage, _, mock_stats = _make_storage(storage_env)
        mock_stats.query.return_value = []

        storage.get_player_game_stats("mahomes-patrick", limit=5)

        assert mock_stats.query.call_args.kwargs["limit"] == 5


class TestGetTeamEvents:
    def test_filters_by_sport_status_and_participant(self, storage_env):
        storage, mock_events, _ = _make_storage(storage_env)
        mock_events.scan.return_value = [
            _event("1", "KC"),
            _event("2", "LAC"),  # different team
            _event("3", "KC", sport="nba"),  # different sport
            _event("4", "KC", status="scheduled"),  # not completed
        ]

        result = storage.get_team_events("nfl", "KC")

        assert [e["event_id"] for e in result] == ["1"]

    def test_sorts_most_recent_first(self, storage_env):
        storage, mock_events, _ = _make_storage(storage_env)
        mock_events.scan.return_value = [
            _event("1", "KC", event_date="2025-09-01"),
            _event("2", "KC", event_date="2025-09-15"),
        ]

        result = storage.get_team_events("nfl", "KC")

        assert [e["event_id"] for e in result] == ["2", "1"]

    def test_before_date_excludes_later_games(self, storage_env):
        storage, mock_events, _ = _make_storage(storage_env)
        mock_events.scan.return_value = [
            _event("1", "KC", event_date="2025-09-01"),
            _event("2", "KC", event_date="2025-09-15"),
        ]

        result = storage.get_team_events("nfl", "KC", before_date="2025-09-10")

        assert [e["event_id"] for e in result] == ["1"]

    def test_limit_truncates_after_sort(self, storage_env):
        storage, mock_events, _ = _make_storage(storage_env)
        mock_events.scan.return_value = [
            _event("1", "KC", event_date="2025-09-01"),
            _event("2", "KC", event_date="2025-09-15"),
            _event("3", "KC", event_date="2025-09-08"),
        ]

        result = storage.get_team_events("nfl", "KC", limit=2)

        assert [e["event_id"] for e in result] == ["2", "3"]
