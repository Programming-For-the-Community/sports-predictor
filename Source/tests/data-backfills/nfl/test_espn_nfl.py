"""
Integration tests for the NFL backfill ESPN client and normalization layer.

These tests hit the real ESPN public API and verify that both the raw
responses have the expected shape AND that normalize.py maps them into
the schema this project writes to AWS. No AWS credentials are needed --
the storage layer is never touched here.

Run from the repo root:
    pytest Source/tests/data-backfills/nfl/

All ESPN calls are made once per session via module-scoped fixtures so
the rate limiter is only hit a handful of times across the full suite.
"""
import pytest

from library.http.nfl import NFLClient
import normalize

TEST_SEASON = 2024
TEST_SEASON_TYPE = 2   # regular season
TEST_WEEK = 1


# ---------------------------------------------------------------------------
# Shared fixtures -- one real API call per fixture for the entire test run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    return NFLClient()


@pytest.fixture(scope="module")
def teams_response(client):
    return client.get_teams()


@pytest.fixture(scope="module")
def scoreboard_response(client):
    return client.get_scoreboard(TEST_SEASON, TEST_SEASON_TYPE, TEST_WEEK)


@pytest.fixture(scope="module")
def first_event(scoreboard_response):
    events = scoreboard_response.get("events", [])
    assert events, "ESPN returned no events for the test week -- pick a different week"
    return events[0]


@pytest.fixture(scope="module")
def summary_response(client, first_event):
    return client.get_summary(first_event["id"])


# ---------------------------------------------------------------------------
# NFL client -- verify the API is reachable and returns expected structure
# ---------------------------------------------------------------------------

class TestNFLClient:
    def test_get_teams_returns_32_nfl_teams(self, teams_response):
        leagues = teams_response["sports"][0]["leagues"]
        assert leagues, "No leagues in teams response"
        teams = leagues[0]["teams"]
        assert len(teams) == 32

    def test_get_scoreboard_has_events(self, scoreboard_response):
        assert "events" in scoreboard_response
        assert len(scoreboard_response["events"]) > 0

    def test_get_scoreboard_event_has_id(self, first_event):
        assert "id" in first_event
        assert first_event["id"]

    def test_get_summary_has_header_and_boxscore(self, summary_response):
        assert "header" in summary_response
        assert "boxscore" in summary_response

    def test_get_summary_header_has_event_id(self, summary_response, first_event):
        assert summary_response["header"]["id"] == first_event["id"]


# ---------------------------------------------------------------------------
# normalize.team_to_entity
# ---------------------------------------------------------------------------

class TestNormalizeTeams:
    def test_entity_has_required_schema_fields(self, teams_response):
        team = teams_response["sports"][0]["leagues"][0]["teams"][0]["team"]
        entity = normalize.team_to_entity(team)

        for field in ("entity_key", "entity_id", "sport", "entity_type", "name", "metadata"):
            assert field in entity, f"Missing field: {field}"

    def test_entity_sport_and_type_are_correct(self, teams_response):
        team = teams_response["sports"][0]["leagues"][0]["teams"][0]["team"]
        entity = normalize.team_to_entity(team)

        assert entity["sport"] == "nfl"
        assert entity["entity_type"] == "team"

    def test_entity_metadata_has_expected_fields(self, teams_response):
        team = teams_response["sports"][0]["leagues"][0]["teams"][0]["team"]
        entity = normalize.team_to_entity(team)

        for field in ("abbreviation", "location", "nickname"):
            assert field in entity["metadata"], f"Missing metadata field: {field}"

    def test_all_32_team_entity_keys_are_unique(self, teams_response):
        league_teams = teams_response["sports"][0]["leagues"][0]["teams"]
        entities = [normalize.team_to_entity(t["team"]) for t in league_teams]
        keys = [e["entity_key"] for e in entities]
        assert len(keys) == len(set(keys)), "Duplicate entity keys across teams"


# ---------------------------------------------------------------------------
# normalize.scoreboard_event_to_event_item
# ---------------------------------------------------------------------------

class TestNormalizeScoreboardEvent:
    def test_event_item_has_required_schema_fields(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)

        for field in ("event_key", "event_id", "sport", "event_type", "event_date", "status", "participants", "season"):
            assert field in item, f"Missing field: {field}"

    def test_event_item_sport_and_type_are_correct(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)

        assert item["sport"] == "nfl"
        assert item["event_type"] == "head_to_head"

    def test_event_item_has_two_participants(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        assert len(item["participants"]) == 2

    def test_participant_fields(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        for participant in item["participants"]:
            assert "entity_id" in participant
            assert "role" in participant
            assert participant["role"] in ("home", "away", "unknown")
            assert "result" in participant
            assert "score" in participant["result"]
            assert "won" in participant["result"]

    def test_status_is_valid_value(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        assert item["status"] in ("completed", "scheduled")

    def test_event_date_is_iso_format(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        # Should be YYYY-MM-DD (10 chars)
        assert len(item["event_date"]) == 10
        assert item["event_date"][4] == "-" and item["event_date"][7] == "-"


# ---------------------------------------------------------------------------
# normalize.boxscore_to_player_game_stats
# ---------------------------------------------------------------------------

class TestNormalizeBoxscore:
    def test_returns_two_lists(self, summary_response):
        stats_items, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        assert isinstance(stats_items, list)
        assert isinstance(player_entities, list)

    def test_stat_items_are_non_empty(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        assert len(stats_items) > 0

    def test_stat_item_has_required_schema_fields(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        for item in stats_items:
            for field in ("event_key", "player_key", "entity_id", "team_id", "event_date", "stat_line"):
                assert field in item, f"Missing field: {field}"

    def test_stat_line_is_dict(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        for item in stats_items:
            assert isinstance(item["stat_line"], dict)
            assert len(item["stat_line"]) > 0

    def test_player_entities_are_non_empty(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        assert len(player_entities) > 0

    def test_player_entity_has_required_schema_fields(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        for entity in player_entities:
            for field in ("entity_key", "entity_id", "sport", "entity_type", "name", "metadata"):
                assert field in entity, f"Missing field: {field}"

    def test_player_entity_sport_and_type_are_correct(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        for entity in player_entities:
            assert entity["sport"] == "nfl"
            assert entity["entity_type"] == "player"

    def test_no_duplicate_player_stat_lines(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        composite_keys = [(item["event_key"], item["player_key"]) for item in stats_items]
        assert len(composite_keys) == len(set(composite_keys)), "Duplicate (event_key, player_key) pairs"

    def test_stat_items_and_player_entities_have_same_count(self, summary_response):
        stats_items, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        assert len(stats_items) == len(player_entities)
