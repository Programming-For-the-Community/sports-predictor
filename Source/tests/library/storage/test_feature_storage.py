"""
Unit tests for FeatureStorage's read methods. DynamoDBTable itself is
mocked -- these verify FeatureStorage builds the right query/scan calls
and filters/sorts scan results correctly, not DynamoDB behavior (see
tests/library/aws/test_dynamodb_table.py for that).
"""
from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.conditions import Key

from library.storage.feature_storage import FeatureStorage


@pytest.fixture
def storage_env(monkeypatch):
    monkeypatch.setenv("ENTITIES_TABLE_NAME", "test-entities")
    monkeypatch.setenv("EVENTS_TABLE_NAME", "test-events")
    monkeypatch.setenv("PLAYER_GAME_STATS_TABLE_NAME", "test-player-game-stats")
    monkeypatch.setenv("TEAM_GAME_STATS_TABLE_NAME", "test-team-game-stats")


def _make_storage(storage_env):
    mock_entities = MagicMock()
    mock_events = MagicMock()
    mock_stats = MagicMock()
    mock_team_stats = MagicMock()
    with patch("library.storage.feature_storage.DynamoDBTable") as mock_table_cls:
        mock_table_cls.side_effect = [mock_entities, mock_events, mock_stats, mock_team_stats]
        storage = FeatureStorage()
    return storage, mock_entities, mock_events, mock_stats, mock_team_stats


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
        storage, _, _, mock_stats, _ = _make_storage(storage_env)
        mock_stats.query.return_value = [{"event_date": "2025-09-28"}]

        result = storage.get_player_game_stats("mahomes-patrick")

        assert result == [{"event_date": "2025-09-28"}]
        call_kwargs = mock_stats.query.call_args.kwargs
        assert call_kwargs["index_name"] == "entity-history"
        assert call_kwargs["scan_index_forward"] is False
        assert call_kwargs["limit"] is None

    def test_passes_limit_through(self, storage_env):
        storage, _, _, mock_stats, _ = _make_storage(storage_env)
        mock_stats.query.return_value = []

        storage.get_player_game_stats("mahomes-patrick", limit=5)

        assert mock_stats.query.call_args.kwargs["limit"] == 5


class TestGetAllEvents:
    def test_queries_status_index_most_recent_first(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.query.return_value = [_event("1", "KC")]

        storage.get_all_events("nfl", status="completed")

        call = mock_events.query.call_args
        assert call.kwargs["index_name"] == "status-index"
        assert call.kwargs["scan_index_forward"] is False

    def test_filters_to_the_requested_sport(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.query.return_value = [
            _event("1", "KC", sport="nfl"),
            _event("2", "KC", sport="nba"),
        ]

        result = storage.get_all_events("nfl")

        assert [e["event_id"] for e in result] == ["1"]

    def test_defaults_to_completed_status(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.query.return_value = []

        storage.get_all_events("nfl")

        condition = mock_events.query.call_args.args[0]
        assert condition == Key("status").eq("completed")


class TestGetTeamEvents:
    def test_filters_by_sport_status_and_participant(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        # get_all_events now Queries the status-index GSI (status is
        # already an exact match via the index, unlike sport/participant,
        # which still need filtering in Python) -- see the "different
        # sport" row below, which a real status-index Query would never
        # even return since it's a different status.
        mock_events.query.return_value = [
            _event("1", "KC"),
            _event("2", "LAC"),  # different team
            _event("3", "KC", sport="nba"),  # different sport
        ]

        result = storage.get_team_events("nfl", "KC")

        assert [e["event_id"] for e in result] == ["1"]

    def test_relies_on_the_index_for_most_recent_first_ordering(self, storage_env):
        # get_all_events no longer sorts in Python -- it trusts the
        # status-index GSI's own scan_index_forward=False guarantee, so
        # this mock reflects what a real query already returns: most
        # recent first. get_team_events just filters/truncates that order,
        # it doesn't re-sort.
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.query.return_value = [
            _event("2", "KC", event_date="2025-09-15"),
            _event("1", "KC", event_date="2025-09-01"),
        ]

        result = storage.get_team_events("nfl", "KC")

        assert [e["event_id"] for e in result] == ["2", "1"]

    def test_before_date_excludes_later_games(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.query.return_value = [
            _event("2", "KC", event_date="2025-09-15"),
            _event("1", "KC", event_date="2025-09-01"),
        ]

        result = storage.get_team_events("nfl", "KC", before_date="2025-09-10")

        assert [e["event_id"] for e in result] == ["1"]

    def test_limit_truncates(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.query.return_value = [
            _event("2", "KC", event_date="2025-09-15"),
            _event("3", "KC", event_date="2025-09-08"),
            _event("1", "KC", event_date="2025-09-01"),
        ]

        result = storage.get_team_events("nfl", "KC", limit=2)

        assert [e["event_id"] for e in result] == ["2", "3"]


class TestGetAllPlayerGameStats:
    def test_queries_sport_index(self, storage_env):
        storage, _, _, mock_stats, _ = _make_storage(storage_env)
        mock_stats.query.return_value = [{"entity_id": "mahomes-patrick", "sport": "nfl"}]

        result = storage.get_all_player_game_stats("nfl")

        assert result == [{"entity_id": "mahomes-patrick", "sport": "nfl"}]
        call = mock_stats.query.call_args
        assert call.args[0] == Key("sport").eq("nfl")
        assert call.kwargs["index_name"] == "sport-index"


class TestGetAllTeamGameStats:
    def test_queries_sport_index(self, storage_env):
        storage, _, _, _, mock_team_stats = _make_storage(storage_env)
        mock_team_stats.query.return_value = [{"team_id": "KC", "sport": "nfl"}]

        result = storage.get_all_team_game_stats("nfl")

        assert result == [{"team_id": "KC", "sport": "nfl"}]
        call = mock_team_stats.query.call_args
        assert call.args[0] == Key("sport").eq("nfl")
        assert call.kwargs["index_name"] == "sport-index"


class TestGetTeamGameStatsForTeam:
    def test_filters_by_team_sorts_and_respects_before_date_and_limit(self, storage_env):
        storage, _, _, _, mock_team_stats = _make_storage(storage_env)
        mock_team_stats.query.return_value = [
            {"team_id": "KC", "event_date": "2025-09-01", "event_key": "SPORT#NFL#EVENT#1"},
            {"team_id": "LAC", "event_date": "2025-09-08", "event_key": "SPORT#NFL#EVENT#2"},  # different team
            {"team_id": "KC", "event_date": "2025-09-15", "event_key": "SPORT#NFL#EVENT#3"},
            {"team_id": "KC", "event_date": "2025-09-22", "event_key": "SPORT#NFL#EVENT#4"},
        ]

        result = storage.get_team_game_stats_for_team("nfl", "KC", before_date="2025-09-20", limit=1)

        assert result == [{"team_id": "KC", "event_date": "2025-09-15", "event_key": "SPORT#NFL#EVENT#3"}]


class TestGetPlayerGameStatsForEvent:
    def test_queries_the_base_table_by_event_key(self, storage_env):
        storage, _, _, mock_stats, _ = _make_storage(storage_env)
        mock_stats.query.return_value = [{"entity_id": "mahomes-patrick"}]

        result = storage.get_player_game_stats_for_event("SPORT#NFL#EVENT#401547417")

        assert result == [{"entity_id": "mahomes-patrick"}]
        mock_stats.query.assert_called_once()
        assert mock_stats.query.call_args.kwargs == {}  # no index_name -- base table query


class TestGetEvent:
    def test_gets_by_event_key(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.get_item.return_value = {"event_key": "SPORT#NFL#EVENT#401547417"}

        result = storage.get_event("SPORT#NFL#EVENT#401547417")

        assert result == {"event_key": "SPORT#NFL#EVENT#401547417"}
        mock_events.get_item.assert_called_once_with({"event_key": "SPORT#NFL#EVENT#401547417"})

    def test_returns_none_when_missing(self, storage_env):
        storage, _, mock_events, _, _ = _make_storage(storage_env)
        mock_events.get_item.return_value = None

        assert storage.get_event("SPORT#NFL#EVENT#missing") is None


class TestGetEntity:
    def test_gets_by_sport_and_type_scoped_entity_key(self, storage_env):
        storage, mock_entities, _, _, _ = _make_storage(storage_env)
        mock_entities.get_item.return_value = {"entity_id": "mahomes-patrick"}

        result = storage.get_entity("nfl", "mahomes-patrick", "player")

        assert result == {"entity_id": "mahomes-patrick"}
        mock_entities.get_item.assert_called_once_with({"entity_key": "SPORT#NFL#ENTITY#PLAYER#mahomes-patrick"})

    def test_team_and_player_types_never_collide_for_the_same_raw_id(self, storage_env):
        storage, mock_entities, _, _, _ = _make_storage(storage_env)
        mock_entities.get_item.return_value = None

        storage.get_entity("nba", "25", "team")

        mock_entities.get_item.assert_called_once_with({"entity_key": "SPORT#NBA#ENTITY#TEAM#25"})


class TestGetTeamEntities:
    def test_queries_team_index(self, storage_env):
        storage, mock_entities, _, _, _ = _make_storage(storage_env)
        mock_entities.query.return_value = [{"entity_id": "mahomes-patrick", "team_key": "SPORT#NFL#TEAM#KC"}]

        result = storage.get_team_entities("nfl", "KC")

        assert result == [{"entity_id": "mahomes-patrick", "team_key": "SPORT#NFL#TEAM#KC"}]
        call = mock_entities.query.call_args
        assert call.args[0] == Key("team_key").eq("SPORT#NFL#TEAM#KC")
        assert call.kwargs["index_name"] == "team-index"
