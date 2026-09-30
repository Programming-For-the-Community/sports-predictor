"""
Tests for the NCAA MBB backfill ESPN client and normalization layer, run
against hand-built ESPN responses (see ../_espn_payloads.py) through the
real NCAAMBBClient with its HTTP session faked -- no network access.
Covers both the endpoints/params the client requests and how normalize.py
maps each response into this project's schema.
"""
import pytest

from _espn_payloads import basketball_summary, fake_session, scoreboard_event, scoreboard_payload, teams_payload
from library.http.ncaambb import NCAAMBBClient
import normalize

TEST_DATE = "20250118"
EVENT_ID = "401705999"


@pytest.fixture
def client():
    ncaambb_client = NCAAMBBClient(min_interval_seconds=0)
    ncaambb_client._session = fake_session({
        "teams": teams_payload(362),
        "scoreboard": scoreboard_payload(scoreboard_event(EVENT_ID, "2025-01-18T17:00Z", "150", "2305", "78", "70")),
        "summary": basketball_summary(EVENT_ID, "2025-01-18T17:00Z", "150", "2305"),
    })
    return ncaambb_client


@pytest.fixture
def teams_response(client):
    return client.get_teams()


@pytest.fixture
def scoreboard_response(client):
    return client.get_scoreboard_for_date(TEST_DATE)


@pytest.fixture
def first_event(scoreboard_response):
    return scoreboard_response["events"][0]


@pytest.fixture
def summary_response(client, first_event):
    return client.get_summary(first_event["id"])


class TestRequests:
    def test_each_call_hits_its_own_endpoint_and_params(self, client):
        client.get_teams()
        client.get_scoreboard_for_date(TEST_DATE)
        client.get_summary(EVENT_ID)

        calls = [(c.args[0].rsplit("/", 1)[-1], c.kwargs["params"]) for c in client._session.get.call_args_list]
        assert calls == [
            ("teams", {"limit": 1000}),
            ("scoreboard", {"dates": TEST_DATE, "groups": 50, "limit": 400}),
            ("summary", {"event": EVENT_ID}),
        ]


class TestNCAAMBBClient:
    def test_get_teams_returns_over_300_d1_teams(self, teams_response):
        # 362 confirmed live 2026-08-19 (see project-ncaambb-onboarding
        # memory) -- asserting "over 300" rather than the exact count
        # since D1 realigns most offseasons and an exact match would make
        # this test brittle for no real benefit.
        leagues = teams_response["sports"][0]["leagues"]
        assert leagues, "No leagues in teams response"
        teams = leagues[0]["teams"]
        assert len(teams) > 300

    def test_get_scoreboard_has_events(self, scoreboard_response):
        assert "events" in scoreboard_response
        assert len(scoreboard_response["events"]) > 0

    def test_get_scoreboard_event_has_id(self, first_event):
        assert "id" in first_event
        assert first_event["id"]

    def test_get_scoreboard_event_is_regular_season(self, first_event):
        # Confirms TEST_DATE landed on a regular-season date -- if this
        # ever fails, the fixed TEST_DATE constant needs updating.
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

        assert entity["sport"] == "ncaambb"
        assert entity["entity_type"] == "team"

    def test_all_team_entity_keys_are_unique(self, teams_response):
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

        assert item["sport"] == "ncaambb"
        assert item["event_type"] == "head_to_head"

    def test_event_item_has_two_participants(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        assert len(item["participants"]) == 2

    def test_event_date_is_iso_format(self, first_event):
        item = normalize.scoreboard_event_to_event_item(first_event)
        assert len(item["event_date"]) == 10
        assert item["event_date"][4] == "-"
        assert item["event_date"][7] == "-"


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
        # NCAA MBB's single unnamed stat category must not fabricate a
        # "misc_" prefix, which would silently break TARGET_STAT
        # field-name matching (same as NBA's own box score).
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        for item in stats_items:
            assert not any(key.startswith("misc_") for key in item["stat_line"]), item["stat_line"]

    def test_stat_line_has_points(self, summary_response):
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        assert any("points" in item["stat_line"] for item in stats_items)

    def test_stat_line_has_separate_offensive_and_defensive_rebounds(self, summary_response):
        # Confirmed live, 2026-08-19: unlike some sports, OREB/DREB are
        # present separately alongside combined REB, so no derivation is
        # needed the way NBA's avg_rebounds mistake required.
        stats_items, _ = normalize.boxscore_to_player_game_stats(summary_response)
        assert any("offensive_rebounds" in item["stat_line"] for item in stats_items)
        assert any("defensive_rebounds" in item["stat_line"] for item in stats_items)

    def test_player_entities_are_non_empty(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        assert len(player_entities) > 0

    def test_player_entity_sport_and_type_are_correct(self, summary_response):
        _, player_entities = normalize.boxscore_to_player_game_stats(summary_response)
        for entity in player_entities:
            assert entity["sport"] == "ncaambb"
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
