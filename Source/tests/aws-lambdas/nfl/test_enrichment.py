"""
Unit tests for ingest/enrichment.py -- coach/injury/depth-chart
enrichment and its S3 TTL cache. All AWS calls are mocked; enrichment.py
takes its S3 client and bucket as explicit function arguments rather than
holding its own, so these tests pass a mock directly instead of patching
module-level state.

enrichment is registered on sys.path by conftest.py (same convention as
predict/'s live_features.py, model_loader.py).
"""
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

import enrichment

BUCKET = "test-bucket"


def _make_s3(existing_keys: set | None = None):
    """Return a mock S3 client backed by an in-memory dict -- head_object/
    get_object see whatever's actually been put_object'd (including by
    the code under test itself, e.g. _cached_or_fetch's own cache
    writes), and 404 for anything else. `existing_keys` seeds
    head_object-only existence (no real body) for tests that only ever
    check existence."""
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _head(**kwargs):
        key = kwargs.get("Key")
        if key in store or key in (existing_keys or set()):
            return {}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body.encode("utf-8") if isinstance(body, str) else body

    mock_s3.head_object.side_effect = _head
    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    return mock_s3


def _prime_cache(mock_s3, key: str, data, fetched_at: str = "2026-01-01T00:00:00+00:00") -> None:
    """Seeds a cache HIT for exactly `key` in a _make_s3 mock, in the same
    shape _cached_or_fetch itself writes via _put_json -- routes through
    the mock's own put_object side_effect, so it lands in the same
    in-memory store _cached_or_fetch's later reads see."""
    mock_s3.put_object(Key=key, Body=json.dumps({"fetched_at": fetched_at, "data": data}))


def _competition_event(event_id: str, home_id: str, away_id: str) -> dict:
    return {
        "id": event_id,
        "status": {"type": {"completed": False}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id}},
                {"homeAway": "away", "team": {"id": away_id}},
            ],
        }],
    }


class TestHomeAwayTeamIds:
    def test_extracts_home_and_away_ids(self):
        event = _competition_event("1", "12", "24")
        assert enrichment.home_away_team_ids(event) == ("12", "24")

    def test_none_for_event_with_no_competitions(self):
        assert enrichment.home_away_team_ids({"id": "1"}) is None

    def test_none_for_event_missing_a_side(self):
        event = {"id": "1", "competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "12"}}]}]}
        assert enrichment.home_away_team_ids(event) is None


class TestFilterDepthChart:
    def test_keeps_only_qb_rb_wr_positions(self):
        raw = {
            "positions": {
                "qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]},
                "lde": {"position": {"abbreviation": "LDE"}, "athletes": [{"id": "2"}]},
                "wr": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "3"}]},
            },
        }
        result = enrichment.filter_depth_chart(raw)
        assert set(result.keys()) == {"qb", "wr"}

    def test_empty_when_no_positions_key(self):
        assert enrichment.filter_depth_chart({}) == {}

    def test_trims_each_athlete_down_to_just_id(self):
        # ESPN's real athlete objects carry ~289KB/team of link metadata
        # (player card, stats, splits, game log, news, bio, each with web
        # + sportscenter:// variants) this project never reads -- only
        # "id" may survive into what gets stored.
        raw = {
            "positions": {
                "qb": {
                    "position": {"abbreviation": "QB", "name": "Quarterback", "id": "8"},
                    "athletes": [{
                        "id": "1", "uid": "s:20~l:28~a:1", "guid": "abc",
                        "links": [{"rel": ["playercard"], "href": "https://espn.com/..."}],
                    }],
                },
            },
        }

        result = enrichment.filter_depth_chart(raw)

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}

    def test_athlete_missing_id_is_dropped(self):
        raw = {"positions": {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"noId": "x"}]}}}

        result = enrichment.filter_depth_chart(raw)

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": []}}


class TestEnrichEvents:
    """Every test here runs against a cold cache (_make_s3's get_object
    404s for everything unless a test primes a key via _prime_cache) --
    same "always fetches fresh" assumption these tests had before
    coaches/depth-charts were cached, since a miss falls straight through
    to fetch. Cache-hit behavior itself is covered by TestCachedOrFetch."""

    def test_attaches_coach_injuries_and_depth_chart_to_home_and_away(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.side_effect = lambda team_id: {
            "positions": {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": f"qb-{team_id}"}]}},
        }
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {
            "12": {"coach_id": "1", "coach_name": "Andy Reid", "experience": 27, "season_win_pct": 0.7},
        }
        core_client.get_team_injuries.side_effect = lambda team_id: [{"entity_id": f"p-{team_id}", "status": "Out"}]

        enrichment.enrich_events(events, 2025, nfl_client, core_client, _make_s3(), BUCKET)

        event = events[0]
        assert event["home_coach"] == {"coach_id": "1", "coach_name": "Andy Reid", "experience": 27, "season_win_pct": 0.7}
        assert event["away_coach"] is None  # team 24 has no entry in get_season_coaches' return value
        assert event["home_injuries"] == [{"entity_id": "p-12", "status": "Out"}]
        assert event["away_injuries"] == [{"entity_id": "p-24", "status": "Out"}]
        assert event["home_depth_chart"] == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "qb-12"}]}}

    def test_malformed_event_is_skipped_without_raising(self):
        events = [{"id": "1"}]  # no competitions -- home_away_team_ids returns None
        nfl_client = MagicMock()
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}

        enrichment.enrich_events(events, 2025, nfl_client, core_client, _make_s3(), BUCKET)  # must not raise

        assert "home_coach" not in events[0]
        core_client.get_team_injuries.assert_not_called()

    def test_coach_fetch_failure_omits_coach_fields_without_raising(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = {"positions": {}}
        core_client = MagicMock()
        core_client.get_season_coaches.side_effect = Exception("ESPN 500")
        core_client.get_team_injuries.return_value = []

        enrichment.enrich_events(events, 2025, nfl_client, core_client, _make_s3(), BUCKET)  # must not raise

        assert events[0]["home_coach"] is None
        assert events[0]["away_coach"] is None

    def test_injury_fetch_failure_for_one_team_does_not_block_the_other(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = {"positions": {}}
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}

        def flaky_injuries(team_id):
            if team_id == "12":
                raise Exception("ESPN timeout")
            return [{"entity_id": "p-24", "status": "Questionable"}]

        core_client.get_team_injuries.side_effect = flaky_injuries

        enrichment.enrich_events(events, 2025, nfl_client, core_client, _make_s3(), BUCKET)  # must not raise

        assert events[0]["home_injuries"] is None
        assert events[0]["away_injuries"] == [{"entity_id": "p-24", "status": "Questionable"}]

    def test_depth_chart_fetch_failure_omits_field_without_raising(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.side_effect = Exception("ESPN 500")
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}
        core_client.get_team_injuries.return_value = []

        enrichment.enrich_events(events, 2025, nfl_client, core_client, _make_s3(), BUCKET)  # must not raise

        assert events[0]["home_depth_chart"] is None
        assert events[0]["away_depth_chart"] is None

    def test_injuries_are_never_cached_even_when_coaches_and_depth_chart_are(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = {"positions": {}}
        core_client = MagicMock()
        core_client.get_season_coaches.return_value = {}
        core_client.get_team_injuries.return_value = []

        mock_s3 = _make_s3()
        enrichment.enrich_events(events, 2025, nfl_client, core_client, mock_s3, BUCKET)
        enrichment.enrich_events(events, 2025, nfl_client, core_client, mock_s3, BUCKET)

        # Coaches/depth-chart each got cached after their first fetch, so
        # the second enrich_events call within the same (still-fresh)
        # cache window shouldn't refetch them -- but injuries have no
        # cache at all, so every call hits ESPN.
        assert core_client.get_season_coaches.call_count == 1
        # 2 teams (home + away), each its own cache key -- both fetched
        # once on the first enrich_events call, neither refetched on the
        # second.
        assert nfl_client.get_depth_chart.call_count == 2
        assert core_client.get_team_injuries.call_count == 4


class TestCachedOrFetch:
    def test_cache_miss_calls_fetch_and_writes_the_result_back(self):
        mock_s3 = _make_s3()
        fetch = MagicMock(return_value={"value": 42})

        result = enrichment._cached_or_fetch(mock_s3, BUCKET, "some/key.json", 7, fetch)

        assert result == {"value": 42}
        fetch.assert_called_once()
        written_key, written_body = mock_s3.put_object.call_args.kwargs["Key"], mock_s3.put_object.call_args.kwargs["Body"]
        assert written_key == "some/key.json"
        written = json.loads(written_body)
        assert written["data"] == {"value": 42}
        assert "fetched_at" in written

    def test_fresh_cache_hit_does_not_call_fetch(self):
        mock_s3 = _make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _prime_cache(mock_s3, "some/key.json", {"value": 1}, fetched_at=fresh)
        fetch = MagicMock()

        result = enrichment._cached_or_fetch(mock_s3, BUCKET, "some/key.json", 7, fetch)

        assert result == {"value": 1}
        fetch.assert_not_called()

    def test_stale_cache_past_ttl_calls_fetch(self):
        mock_s3 = _make_s3()
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _prime_cache(mock_s3, "some/key.json", {"value": 1}, fetched_at=stale)
        fetch = MagicMock(return_value={"value": 2})

        result = enrichment._cached_or_fetch(mock_s3, BUCKET, "some/key.json", 7, fetch)

        assert result == {"value": 2}
        fetch.assert_called_once()

    def test_fetch_failure_propagates_rather_than_masking_with_stale_data(self):
        mock_s3 = _make_s3()
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _prime_cache(mock_s3, "some/key.json", {"value": 1}, fetched_at=stale)
        fetch = MagicMock(side_effect=RuntimeError("ESPN 500"))

        try:
            enrichment._cached_or_fetch(mock_s3, BUCKET, "some/key.json", 7, fetch)
            assert False, "expected RuntimeError to propagate"
        except RuntimeError:
            pass
