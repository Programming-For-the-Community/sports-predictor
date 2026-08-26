"""
Integration tests for the PGA backfill ESPN client and normalization
layer.

These tests hit the real ESPN public API and verify that both the raw
responses have the expected shape AND that normalize.py maps them into
the schema this project writes to AWS. No AWS credentials are needed --
the storage layer is never touched here.

Run from the repo root:
    pytest Source/tests/data-backfills/pga/

All ESPN calls are made once per session via module-scoped fixtures so
the rate limiter is only hit a handful of times across the full suite.

TEST_DATE/TEST_EVENT_ID are a fixed, well-in-the-past completed
tournament (the 2021 AT&T Pebble Beach Pro-Am) so the suite stays
deterministic regardless of today's actual PGA schedule -- confirmed live
2026-08-24 to still return STATUS_FINAL.

A persistent ESPN block after HttpClient's own retry-with-backoff is
treated as "unreachable from here right now" (skip) rather than a code
defect (fail), via _fetch_or_skip below.
"""
import pytest

from library.http.pga import PGAClient
import normalize

TEST_DATE = "20210214"  # 2021 AT&T Pebble Beach Pro-Am, well in the past


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
    return PGAClient()


@pytest.fixture(scope="module")
def scoreboard_response(client):
    return _fetch_or_skip("GET scoreboard", lambda: client.get_scoreboard_for_date(TEST_DATE))


@pytest.fixture(scope="module")
def first_event_id(scoreboard_response):
    events = scoreboard_response.get("events", [])
    assert events, "ESPN returned no events for the test date -- pick a different date"
    return events[0]["id"]


@pytest.fixture(scope="module")
def leaderboard_response(client, first_event_id):
    return _fetch_or_skip("GET leaderboard", lambda: client.get_leaderboard(first_event_id))


@pytest.fixture(scope="module")
def leaderboard_event(leaderboard_response):
    events = leaderboard_response.get("events", [])
    assert events, "ESPN returned no events in the leaderboard response"
    return events[0]


# ---------------------------------------------------------------------------
# PGA client -- verify the API is reachable and returns expected structure
# ---------------------------------------------------------------------------

class TestPGAClient:
    def test_get_scoreboard_has_events(self, scoreboard_response):
        assert "events" in scoreboard_response
        assert len(scoreboard_response["events"]) > 0

    def test_get_scoreboard_has_a_season_calendar(self, scoreboard_response):
        # One scoreboard call resolves the whole season's tournament list --
        # see PGAClient.get_scoreboard_for_date's own docstring.
        calendar = scoreboard_response["leagues"][0]["calendar"]
        assert len(calendar) > 30

    def test_get_leaderboard_returns_the_requested_event(self, leaderboard_event, first_event_id):
        assert leaderboard_event["id"] == first_event_id

    def test_get_leaderboard_has_competitors_with_position_and_earnings(self, leaderboard_event):
        # The richer endpoint over the plain scoreboard -- see
        # PGAClient.get_leaderboard's own docstring.
        competitors = leaderboard_event["competitions"][0]["competitors"]
        assert competitors
        assert "position" in competitors[0]["status"]
        assert "earnings" in competitors[0]


# ---------------------------------------------------------------------------
# normalize.leaderboard_event_to_event_item
# ---------------------------------------------------------------------------

class TestNormalizeEventItem:
    def test_event_item_has_required_schema_fields(self, leaderboard_event):
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)

        for field in ("event_key", "event_id", "sport", "event_type", "event_date", "status", "participants", "season"):
            assert field in item, f"Missing field: {field}"

    def test_event_item_sport_and_type_are_correct(self, leaderboard_event):
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)

        assert item["sport"] == "pga"
        assert item["event_type"] == "field"

    def test_event_item_status_is_completed(self, leaderboard_event):
        # TEST_DATE is a completed historical tournament.
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)
        assert item["status"] == "completed"

    def test_event_item_has_many_participants(self, leaderboard_event):
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)
        assert len(item["participants"]) > 10

    def test_every_participant_has_a_non_empty_result_status(self, leaderboard_event):
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)
        for participant in item["participants"]:
            assert participant["result"]["status"]

    def test_at_least_one_participant_finished(self, leaderboard_event):
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)
        statuses = {p["result"]["status"] for p in item["participants"]}
        assert "finished" in statuses

    def test_event_date_is_iso_format(self, leaderboard_event):
        item = normalize.leaderboard_event_to_event_item(leaderboard_event)
        assert len(item["event_date"]) == 10
        assert item["event_date"][4] == "-" and item["event_date"][7] == "-"


# ---------------------------------------------------------------------------
# normalize.leaderboard_event_to_player_entities
# ---------------------------------------------------------------------------

class TestNormalizePlayerEntities:
    def test_returns_a_non_empty_list(self, leaderboard_event):
        entities = normalize.leaderboard_event_to_player_entities(leaderboard_event)
        assert len(entities) > 10

    def test_entity_sport_and_type_are_correct(self, leaderboard_event):
        entities = normalize.leaderboard_event_to_player_entities(leaderboard_event)
        for entity in entities:
            assert entity["sport"] == "pga"
            assert entity["entity_type"] == "player"

    def test_all_entity_keys_are_unique(self, leaderboard_event):
        entities = normalize.leaderboard_event_to_player_entities(leaderboard_event)
        keys = [e["entity_key"] for e in entities]
        assert len(keys) == len(set(keys)), "Duplicate entity keys across competitors"
