"""
Unit tests for live-scores/live_scores.py -- candidate gating (which
events are even worth an ESPN call this cycle), the ESPN response ->
cached-state extraction (verified against a real live MLB scoreboard
response's status shape, since NFL games weren't in season while this
was built), and the S3 cache's read/write/staleness behavior. All AWS
calls are mocked; live_scores.py takes s3/bucket/storage/client as
explicit arguments rather than holding its own, same convention
enrichment.py already uses.

live_scores is registered on sys.path by conftest.py.
"""
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

import live_scores

BUCKET = "test-bucket"
SPORT = "nfl"


def _make_s3():
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body.encode("utf-8") if isinstance(body, str) else body

    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    mock_s3._store = store
    return mock_s3


def _event(event_id: str, kickoff_time: str) -> dict:
    return {"event_id": event_id, "kickoff_time": kickoff_time, "status": "scheduled"}


# Trimmed to just what live_scores.py reads -- verified against the real
# ESPN response for a live MLB game (competitions[0].status.type.state
# =="in", completed=False, shortDetail="Bot 9th"); NFL's own scoreboard
# shares the same site-API status shape.
def _espn_event(event_id: str, *, state: str, completed: bool, detail: str, home_score, away_score) -> dict:
    return {
        "id": event_id,
        "competitions": [{
            "status": {"type": {"state": state, "completed": completed, "shortDetail": detail}},
            "competitors": [
                {"homeAway": "home", "score": home_score},
                {"homeAway": "away", "score": away_score},
            ],
        }],
    }


def _espn_summary(event_id: str) -> dict:
    return {
        "header": {"id": event_id, "competitions": [{"date": "2026-09-14T17:00Z"}]},
        "boxscore": {
            "players": [{
                "team": {"id": "10"},
                "statistics": [{
                    "name": "passing",
                    "keys": ["completions/passingAttempts", "passingYards"],
                    "athletes": [{
                        "athlete": {"id": "100", "displayName": "QB One", "jersey": "9", "position": {"abbreviation": "QB"}},
                        "stats": ["20/30", "250"],
                    }],
                }],
            }],
        },
    }


class TestCandidateEvents:
    def test_excludes_an_event_more_than_15_minutes_before_kickoff(self):
        now = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-09-14T17:16:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, set())

        assert candidates == []

    def test_includes_an_event_within_15_minutes_of_kickoff(self):
        now = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-09-14T17:14:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, set())

        assert [e["event_id"] for e in candidates] == ["1"]

    def test_excludes_an_event_past_the_safety_cap(self):
        now = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-09-14T09:00:00Z")]  # 8h before now

        candidates = live_scores._candidate_events(storage, SPORT, now, set())

        assert candidates == []

    def test_excludes_an_event_already_confirmed_completed_by_a_prior_cycle(self):
        now = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", "2026-09-14T17:00:00Z")]

        candidates = live_scores._candidate_events(storage, SPORT, now, already_completed={"1"})

        assert candidates == []

    def test_skips_an_event_with_no_kickoff_time(self):
        now = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
        storage = MagicMock()
        storage.get_all_events.return_value = [{"event_id": "1"}]  # no kickoff_time key

        candidates = live_scores._candidate_events(storage, SPORT, now, set())

        assert candidates == []


class TestExtractLiveState:
    def test_parses_a_live_game_matching_the_real_espn_response_shape(self):
        espn_event = _espn_event("1", state="in", completed=False, detail="Bot 9th", home_score="2", away_score="6")

        state = live_scores._extract_live_state(espn_event)

        assert state == {"live": True, "completed": False, "detail": "Bot 9th", "home_score": 2, "away_score": 6}

    def test_a_not_yet_started_game_is_not_live(self):
        espn_event = _espn_event("1", state="pre", completed=False, detail="7:00 PM", home_score="0", away_score="0")

        state = live_scores._extract_live_state(espn_event)

        assert state["live"] is False

    def test_a_final_game_is_completed_not_live(self):
        espn_event = _espn_event("1", state="post", completed=True, detail="Final", home_score="4", away_score="2")

        state = live_scores._extract_live_state(espn_event)

        assert state["live"] is False
        assert state["completed"] is True


class TestRefresh:
    def test_no_candidates_never_calls_espn(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        client = MagicMock()
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}
        client.get_scoreboard_for_date.assert_not_called()
        s3.put_object.assert_not_called()

    def test_a_live_candidate_gets_fetched_and_cached(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", datetime.now(timezone.utc).isoformat())]
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {
            "events": [_espn_event("1", state="in", completed=False, detail="Q3", home_score="10", away_score="7")],
        }
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 1}
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["live"] is True
        assert cached["events"]["1"]["home_score"] == 10

    def test_a_candidate_missing_from_todays_espn_scoreboard_is_skipped_not_errored(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", datetime.now(timezone.utc).isoformat())]
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {"events": []}
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}

    def test_an_event_confirmed_completed_last_cycle_is_not_repolled(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", datetime.now(timezone.utc).isoformat())]
        client = MagicMock()
        s3 = _make_s3()
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "events": {"1": {"live": False, "completed": True, "detail": "Final", "home_score": 20, "away_score": 14}},
        }).encode("utf-8")

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 0}
        client.get_scoreboard_for_date.assert_not_called()


class TestLivePlayerStats:
    def test_a_live_candidates_boxscore_is_fetched_and_cached(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", datetime.now(timezone.utc).isoformat())]
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {
            "events": [_espn_event("1", state="in", completed=False, detail="Q3", home_score="10", away_score="7")],
        }
        client.get_summary.return_value = _espn_summary("1")
        s3 = _make_s3()

        live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        client.get_summary.assert_called_once_with("1")
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["player_stats"]["100"] == {
            "completions": 20, "passing_attempts": 30, "passing_yards": 250,
        }

    def test_a_not_yet_live_candidate_never_fetches_a_boxscore(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", datetime.now(timezone.utc).isoformat())]
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {
            "events": [_espn_event("1", state="pre", completed=False, detail="7:00 PM", home_score="0", away_score="0")],
        }
        s3 = _make_s3()

        live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        client.get_summary.assert_not_called()

    def test_a_boxscore_fetch_failure_omits_player_stats_but_does_not_fail_the_whole_refresh(self):
        storage = MagicMock()
        storage.get_all_events.return_value = [_event("1", datetime.now(timezone.utc).isoformat())]
        client = MagicMock()
        client.get_scoreboard_for_date.return_value = {
            "events": [_espn_event("1", state="in", completed=False, detail="Q3", home_score="10", away_score="7")],
        }
        client.get_summary.side_effect = RuntimeError("ESPN hiccup")
        s3 = _make_s3()

        result = live_scores.refresh(storage, s3, BUCKET, client, SPORT)

        assert result == {"polled": 1}
        cached = json.loads(s3._store[live_scores.LIVE_SCORES_CACHE_KEY])
        assert cached["events"]["1"]["live"] is True
        assert cached["events"]["1"]["player_stats"] == {}

    def test_live_player_stats_parses_the_boxscore_into_stat_lines_keyed_by_entity_id(self):
        client = MagicMock()
        client.get_summary.return_value = _espn_summary("1")

        stats = live_scores._live_player_stats(client, SPORT, "1")

        assert stats == {"100": {"completions": 20, "passing_attempts": 30, "passing_yards": 250}}

    def test_live_player_stats_returns_empty_on_a_malformed_response_instead_of_raising(self):
        client = MagicMock()
        client.get_summary.return_value = {"unexpected": "shape"}

        stats = live_scores._live_player_stats(client, SPORT, "1")

        assert stats == {}


class TestGetLiveScores:
    def test_returns_empty_when_nothing_cached_yet(self):
        s3 = _make_s3()

        assert live_scores.get_live_scores(s3, BUCKET) == {"events": {}}

    def test_returns_cached_events_when_fresh(self):
        s3 = _make_s3()
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "events": {"1": {"live": True, "completed": False, "detail": "Q3", "home_score": 10, "away_score": 7}},
        }).encode("utf-8")

        result = live_scores.get_live_scores(s3, BUCKET)

        assert result["events"]["1"]["live"] is True

    def test_returns_empty_when_the_cache_is_stale(self):
        s3 = _make_s3()
        stale_fetch = datetime.now(timezone.utc) - live_scores.STALE_AFTER - timedelta(minutes=1)
        s3._store[live_scores.LIVE_SCORES_CACHE_KEY] = json.dumps({
            "fetched_at": stale_fetch.isoformat(),
            "events": {"1": {"live": True, "completed": False, "detail": "Q3", "home_score": 10, "away_score": 7}},
        }).encode("utf-8")

        result = live_scores.get_live_scores(s3, BUCKET)

        assert result == {"events": {}}
