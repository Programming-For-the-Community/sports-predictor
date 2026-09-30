"""
Tests for the PGA backfill ESPN client and normalization layer, run against
hand-built ESPN responses (see ../_espn_payloads.py) through the real
PGAClient with its HTTP session faked -- no network access. Covers both
the endpoints/params the client requests and how normalize.py maps a
leaderboard into this project's schema.
"""
import pytest

from _espn_payloads import fake_session, golf_competitor, golf_leaderboard_event
from library.http.pga import PGAClient
import normalize

TEST_DATE = "20210214"
EVENT_ID = "401219478"


@pytest.fixture
def client():
    competitors = [golf_competitor(str(1000 + i), f"T{i}" if i % 3 else str(i), f"-{20 - i}") for i in range(1, 12)]
    competitors.append(golf_competitor("2000", "-", "+4", status_name="STATUS_CUT"))
    pga_client = PGAClient(min_interval_seconds=0)
    pga_client._session = fake_session({
        "pga/scoreboard": {
            "events": [{"id": EVENT_ID}],
            "leagues": [{"calendar": [{"id": str(400000000 + i)} for i in range(45)]}],
        },
        "leaderboard": {"events": [golf_leaderboard_event(EVENT_ID, competitors)]},
    })
    return pga_client


@pytest.fixture
def scoreboard_response(client):
    return client.get_scoreboard_for_date(TEST_DATE)


@pytest.fixture
def first_event_id(scoreboard_response):
    return scoreboard_response["events"][0]["id"]


@pytest.fixture
def leaderboard_response(client, first_event_id):
    return client.get_leaderboard(first_event_id)


@pytest.fixture
def leaderboard_event(leaderboard_response):
    return leaderboard_response["events"][0]


class TestRequests:
    def test_each_call_hits_its_own_endpoint_and_params(self, client):
        client.get_scoreboard_for_date(TEST_DATE)
        client.get_leaderboard(EVENT_ID)

        urls = [c.args[0] for c in client._session.get.call_args_list]
        assert urls[0].endswith("/pga/scoreboard")
        assert urls[1].endswith("/leaderboard")
        assert [c.kwargs["params"] for c in client._session.get.call_args_list] == [{"dates": TEST_DATE}, {"event": EVENT_ID}]


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
        assert item["event_date"][4] == "-"
        assert item["event_date"][7] == "-"


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
