"""
Integration tests for the NBA backfill ESPN client and normalization layer.

These tests hit the real ESPN public API and verify that both the raw
responses have the expected shape AND that normalize.py maps them into
the schema this project writes to AWS. No AWS credentials are needed --
the storage layer is never touched here.

Run from the repo root:
    pytest Source/tests/data-backfills/nba/

All ESPN calls are made once per session via module-scoped fixtures so
the rate limiter is only hit a handful of times across the full suite.

TEST_DATE is a fixed, well-in-the-past regular-season date so the suite
stays deterministic regardless of today's actual NBA schedule.

A persistent ESPN block after HttpClient's own retry-with-backoff is
treated as "unreachable from here right now" (skip) rather than a code
defect (fail), via _fetch_or_skip below.
"""
import pytest

from library.http.nba import NBAClient
import normalize

TEST_DATE = "20250115"  # 2024-25 regular season, well in the past


def _fetch_or_skip(description: str, fetch):
    try:
        return fetch()
    except RuntimeError as exc:
        pytest.skip(f"ESPN unreachable from this network ({description}): {exc}")


# ---------------------------------------------------------------------------
# Shared fixtures -- one real API call per fixture for the entire test run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    return NBAClient()


@pytest.fixture(scope="module")
def teams_response(client):
    return _fetch_or_skip("GET teams", client.get_teams)


@pytest.fixture(scope="module")
def scoreboard_response(client):
    return _fetch_or_skip("GET scoreboard", lambda: client.get_scoreboard_for_date(TEST_DATE))


@pytest.fixture(scope="module")
def first_event(scoreboard_response):
    events = scoreboard_response.get("events", [])
    assert events, "ESPN returned no events for the test date -- pick a different date"
    return events[0]


@pytest.fixture(scope="module")
def summary_response(client, first_event):
    return _fetch_or_skip("GET summary", lambda: client.get_summary(first_event["id"]))


# ---------------------------------------------------------------------------
# NBA client -- verify the API is reachable and returns expected structure
# ---------------------------------------------------------------------------

class TestNBAClient:
    def test_get_teams_returns_30_nba_teams(self, teams_response):
        leagues = teams_response["sports"][0]["leagues"]
        assert leagues, "No leagues in teams response"
        teams = leagues[0]["teams"]
        assert len(teams) == 30

    def test_get_scoreboard_has_events(self, scoreboard_response):
        assert "events" in scoreboard_response
        assert len(scoreboard_response["events"]) > 0

    def test_get_scoreboard_event_has_id(self, first_event):
        assert "id" in first_event
        assert first_event["id"]

    def test_get_scoreboard_event_is_regular_season(self, first_event):
        # Confirms TEST_DATE landed on a regular-season date, not preseason
        # -- if this ever fails, the fixed TEST_DATE constant needs updating.
        assert first_event["season"]["type"] == 2

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

        assert entity["sport"] == "nba"
        assert entity["entity_type"] == "team"

    def test_all_30_team_entity_keys_are_unique(self, teams_response):
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

        assert item["sport"] == "nba"
        assert item["event_type"] == "head_to_head"

    def test_event_item_has_two_participants(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        assert len(item["participants"]) == 2

    def test_event_date_is_iso_format(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
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

    def test_stat_line_has_no_misc_prefixed_keys(self, summary_response):
        # NBA's single unnamed stat category must not fabricate a "misc_"
        # prefix, which would silently break TARGET_STAT field-name matching.
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        for item in stats_items:
            assert not any(key.startswith("misc_") for key in item["stat_line"]), item["stat_line"]

    def test_stat_line_has_points(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        assert any("points" in item["stat_line"] for item in stats_items)

    def test_player_entities_are_non_empty(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        assert len(player_entities) > 0

    def test_player_entity_sport_and_type_are_correct(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        for entity in player_entities:
            assert entity["sport"] == "nba"
            assert entity["entity_type"] == "player"

    def test_no_duplicate_player_stat_lines(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        composite_keys = [(item["event_key"], item["player_key"]) for item in stats_items]
        assert len(composite_keys) == len(set(composite_keys)), "Duplicate (event_key, player_key) pairs"


# ---------------------------------------------------------------------------
# normalize.boxscore_to_team_game_stats
# ---------------------------------------------------------------------------

class TestNormalizeTeamGameStats:
    def test_returns_two_team_items(self, summary_response):
        items = normalize.boxscore_to_team_game_stats(summary_response)
        assert len(items) == 2

    def test_item_has_required_schema_fields(self, summary_response):
        items = normalize.boxscore_to_team_game_stats(summary_response)
        for item in items:
            for field in ("event_key", "team_key", "team_id", "event_date", "stat_line"):
                assert field in item, f"Missing field: {field}"

    def test_stat_line_is_non_empty_dict(self, summary_response):
        items = normalize.boxscore_to_team_game_stats(summary_response)
        for item in items:
            assert isinstance(item["stat_line"], dict)
            assert len(item["stat_line"]) > 0

    def test_no_duplicate_team_keys(self, summary_response):
        items = normalize.boxscore_to_team_game_stats(summary_response)
        team_keys = [item["team_key"] for item in items]
        assert len(team_keys) == len(set(team_keys))
