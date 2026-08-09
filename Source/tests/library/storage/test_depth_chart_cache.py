"""
Unit tests for library.storage.depth_chart_cache -- shared by
aws-lambdas/nfl/ingest/enrichment.py and aws-lambdas/nfl/schedule-sync/
handler.py. All AWS calls are mocked; every function here takes its S3
client and bucket as explicit arguments rather than holding its own, so
these tests pass a mock directly instead of patching module-level state.
"""
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from library.storage.depth_chart_cache import (
    attach_depth_charts,
    filter_depth_chart,
    get_cached_depth_chart,
    home_away_team_ids,
)

BUCKET = "test-bucket"


def _make_s3(existing_keys: set | None = None):
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
    mock_s3.put_object(Key=key, Body=json.dumps({"fetched_at": fetched_at, "data": data}))


def _competition_event(event_id: str, home_id: str, away_id: str) -> dict:
    return {
        "id": event_id,
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
        assert home_away_team_ids(event) == ("12", "24")

    def test_none_for_event_with_no_competitions(self):
        assert home_away_team_ids({"id": "1"}) is None

    def test_none_for_event_missing_a_side(self):
        event = {"id": "1", "competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "12"}}]}]}
        assert home_away_team_ids(event) is None

    def test_none_for_undetermined_playoff_bracket_slot(self):
        # ESPN's real placeholder for a postseason matchup nobody's
        # qualified for yet -- confirmed live against an early-season
        # postseason scoreboard.
        event = _competition_event("1", "-1", "-2")
        assert home_away_team_ids(event) is None


def _raw_depth_chart(*groups: dict) -> dict:
    """Matches ESPN's real depthcharts response shape (confirmed live via
    curl against site.web.api.espn.com) -- a top-level `depthchart` list
    of formation-specific groups (e.g. "Base 3-4 D", "3WR 1TE"), each
    with its OWN `positions` dict. Each arg is one group's `positions`
    dict; the group's own id/name don't matter to filter_depth_chart."""
    return {"depthchart": [{"id": str(i), "name": f"group-{i}", "positions": positions} for i, positions in enumerate(groups)]}


class TestFilterDepthChart:
    def test_keeps_only_qb_rb_wr_positions(self):
        raw = _raw_depth_chart({
            "qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]},
            "lde": {"position": {"abbreviation": "LDE"}, "athletes": [{"id": "2"}]},
            "wr1": {"position": {"abbreviation": "WR"}, "athletes": [{"id": "3"}]},
        })
        result = filter_depth_chart(raw)
        assert set(result.keys()) == {"qb", "wr1"}

    def test_empty_when_no_depthchart_key(self):
        assert filter_depth_chart({}) == {}

    def test_merges_positions_across_every_formation_group(self):
        # The real response splits offense/defense/special-teams into
        # separate groups -- QB/RB/WR only ever live in one of them, but
        # filter_depth_chart still has to look at all of them to find it.
        raw = _raw_depth_chart(
            {"lde": {"position": {"abbreviation": "LDE"}, "athletes": [{"id": "2"}]}},
            {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}},
        )
        result = filter_depth_chart(raw)
        assert set(result.keys()) == {"qb"}

    def test_trims_each_athlete_down_to_just_id(self):
        raw = _raw_depth_chart({
            "qb": {
                "position": {"abbreviation": "QB", "name": "Quarterback", "id": "8"},
                "athletes": [{
                    "id": "1", "uid": "s:20~l:28~a:1", "guid": "abc",
                    "links": [{"rel": ["playercard"], "href": "https://espn.com/..."}],
                }],
            },
        })

        result = filter_depth_chart(raw)

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}

    def test_athlete_missing_id_is_dropped(self):
        raw = _raw_depth_chart({"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"noId": "x"}]}})

        result = filter_depth_chart(raw)

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": []}}


class TestGetCachedDepthChart:
    def test_cache_miss_fetches_filters_and_caches(self):
        mock_s3 = _make_s3()
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = _raw_depth_chart(
            {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}},
        )

        result = get_cached_depth_chart(mock_s3, BUCKET, nfl_client, "12")

        assert result == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}
        nfl_client.get_depth_chart.assert_called_once_with("12")
        mock_s3.put_object.assert_called_once()

    def test_fresh_cache_hit_does_not_call_the_client(self):
        mock_s3 = _make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _prime_cache(mock_s3, "nfl/cache/depth-charts/12.json", {"qb": "cached"}, fetched_at=fresh)
        nfl_client = MagicMock()

        result = get_cached_depth_chart(mock_s3, BUCKET, nfl_client, "12")

        assert result == {"qb": "cached"}
        nfl_client.get_depth_chart.assert_not_called()

    def test_stale_cache_refetches(self):
        mock_s3 = _make_s3()
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _prime_cache(mock_s3, "nfl/cache/depth-charts/12.json", {"qb": "stale"}, fetched_at=stale)
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = _raw_depth_chart({})

        get_cached_depth_chart(mock_s3, BUCKET, nfl_client, "12")

        nfl_client.get_depth_chart.assert_called_once_with("12")


class TestAttachDepthCharts:
    def test_attaches_to_home_and_away(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.side_effect = lambda team_id: _raw_depth_chart(
            {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": f"qb-{team_id}"}]}},
        )

        attach_depth_charts(events, nfl_client, _make_s3(), BUCKET)

        event = events[0]
        assert event["home_depth_chart"] == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "qb-12"}]}}
        assert event["away_depth_chart"] == {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "qb-24"}]}}

    def test_fetches_each_team_at_most_once_across_events(self):
        # Both events share team 12 -- confirms the per-run team_ids set
        # dedupes before fetching, not once per event referencing it.
        events = [_competition_event("1", "12", "24"), _competition_event("2", "12", "7")]
        nfl_client = MagicMock()
        nfl_client.get_depth_chart.return_value = _raw_depth_chart({})

        attach_depth_charts(events, nfl_client, _make_s3(), BUCKET)

        assert nfl_client.get_depth_chart.call_count == 3  # teams 12, 24, 7

    def test_malformed_event_is_skipped_without_raising(self):
        events = [{"id": "1"}]
        nfl_client = MagicMock()

        attach_depth_charts(events, nfl_client, _make_s3(), BUCKET)  # must not raise

        assert "home_depth_chart" not in events[0]

    def test_undetermined_playoff_bracket_slot_never_fetches_a_depth_chart(self):
        events = [_competition_event("1", "-1", "-2")]
        nfl_client = MagicMock()

        attach_depth_charts(events, nfl_client, _make_s3(), BUCKET)  # must not raise

        assert "home_depth_chart" not in events[0]
        nfl_client.get_depth_chart.assert_not_called()

    def test_fetch_failure_for_one_team_omits_only_that_field(self):
        events = [_competition_event("1", "12", "24")]
        nfl_client = MagicMock()

        def flaky(team_id):
            if team_id == "12":
                raise Exception("ESPN 500")
            return _raw_depth_chart({})

        nfl_client.get_depth_chart.side_effect = flaky

        attach_depth_charts(events, nfl_client, _make_s3(), BUCKET)  # must not raise

        assert events[0]["home_depth_chart"] is None
        assert events[0]["away_depth_chart"] == {}
