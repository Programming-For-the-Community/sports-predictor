"""
Unit tests for aws-lambdas/f1/live-scores/live_scores.py -- the ESPN-
sourced live-timing cache, genuinely different from every other F1
module (Jolpica-sourced). Covers the two cross-provider joins (driver by
normalized name, event by calendar date) plus the refresh/caching logic.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

import live_scores

_NO_CACHE_YET = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")


def _field_event(event_id, event_date, participants, status="completed"):
    return {
        "event_key": f"SPORT#F1#EVENT#{event_id}", "event_id": event_id, "event_type": "field",
        "event_date": event_date, "status": status, "participants": participants,
    }


def _sprint_event(event_id, event_date, status="completed"):
    return {
        "event_key": f"SPORT#F1#EVENT#{event_id}", "event_id": event_id, "event_type": "sprint",
        "event_date": event_date, "status": status, "participants": [],
    }


def _espn_competitor(order, name, winner=False):
    return {"order": order, "winner": winner, "athlete": {"fullName": name, "displayName": name}}


def _espn_competition(competition_id, session_type, date, state, competitors):
    return {
        "id": competition_id, "type": {"abbreviation": session_type}, "date": date,
        "status": {"type": {"state": state, "name": f"STATUS_{state.upper()}"}},
        "competitors": competitors,
    }


def _espn_event(name, competitions):
    return {"name": name, "competitions": competitions}


class TestNormalizeName:
    def test_lowercases_and_strips_accents(self):
        assert live_scores._normalize_name("Nico Hülkenberg") == "nico hulkenberg"

    def test_matches_regardless_of_accent_presence(self):
        assert live_scores._normalize_name("Nico Hülkenberg") == live_scores._normalize_name("Nico Hulkenberg")

    def test_collapses_punctuation_and_whitespace(self):
        assert live_scores._normalize_name("  Max   Verstappen! ") == "max verstappen"


class TestCurrentRosterByName:
    def test_resolves_from_the_most_recently_completed_field_race(self):
        older = _field_event("2026-1", "2026-03-08", [{"entity_id": "driver_a"}])
        newer = _field_event("2026-2", "2026-03-15", [{"entity_id": "max_verstappen"}])
        storage = MagicMock()
        storage.get_all_events.return_value = [older, newer]
        storage.get_entity.return_value = {"name": "Max Verstappen"}

        lookup = live_scores._current_roster_by_name(storage, "f1")

        assert lookup == {"max verstappen": "max_verstappen"}

    def test_no_completed_field_race_returns_an_empty_lookup(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        assert live_scores._current_roster_by_name(storage, "f1") == {}


class TestEventIdsByDateAndType:
    def test_builds_from_both_completed_and_scheduled_events(self):
        completed = _field_event("2026-1", "2026-03-08", [])
        scheduled = _sprint_event("2026-5-sprint", "2026-05-24", status="scheduled")
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status="completed": {
            "completed": [completed], "scheduled": [scheduled],
        }.get(status, [])

        lookup = live_scores._event_ids_by_date_and_type(storage, "f1")

        assert lookup[("2026-03-08", "field")] == "2026-1"
        assert lookup[("2026-05-24", "sprint")] == "2026-5-sprint"


class TestRefresh:
    def _storage(self, roster_event, scheduled_events=()):
        """roster_event is the one already-COMPLETED field race
        _current_roster_by_name resolves names from. scheduled_events is
        whatever's still "scheduled" in OUR OWN Jolpica-sourced storage --
        the race a live ESPN poll is actually about is still "scheduled"
        on our own side (Jolpica has no live-timing at all, so it hasn't
        ingested real results for it yet), which is exactly why
        _event_ids_by_date_and_type checks BOTH statuses."""
        storage = MagicMock()

        def _get_all_events(sport, status="completed"):
            if status == "completed":
                return [roster_event]
            if status == "scheduled":
                return list(scheduled_events)
            return []
        storage.get_all_events.side_effect = _get_all_events
        storage.get_entity.return_value = {"name": "Max Verstappen"}
        return storage

    def test_empty_scoreboard_polls_nothing(self):
        storage = MagicMock()
        s3 = MagicMock()
        s3.get_object.side_effect = _NO_CACHE_YET
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": []}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        assert result == {"polled": 0}

    def test_a_live_race_is_cached_keyed_by_our_own_event_id_with_matched_participants(self):
        roster_event = _field_event("2026-1", "2026-03-01", [{"entity_id": "max_verstappen"}])
        this_race = _field_event("2026-2", "2026-03-08", [], status="scheduled")
        storage = self._storage(roster_event, [this_race])

        s3 = MagicMock()
        s3.get_object.side_effect = _NO_CACHE_YET
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": [
            _espn_event("Australian Grand Prix", [
                _espn_competition("999", "Race", "2026-03-08T04:00Z", "in", [_espn_competitor(1, "Max Verstappen", winner=True)]),
            ]),
        ]}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        assert result == {"polled": 1}
        cached_body = json.loads(s3.put_object.call_args.kwargs["Body"])
        entry = cached_body["events"]["2026-2"]
        assert entry["event_type"] == "field"
        assert entry["state"] == "in"
        assert entry["participants"]["max_verstappen"] == {"order": 1, "winner": True}

    def test_an_unmatched_espn_competitor_name_is_skipped_not_a_crash(self):
        roster_event = _field_event("2026-1", "2026-03-01", [{"entity_id": "max_verstappen"}])
        this_race = _field_event("2026-2", "2026-03-08", [], status="scheduled")
        storage = self._storage(roster_event, [this_race])

        s3 = MagicMock()
        s3.get_object.side_effect = _NO_CACHE_YET
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": [
            _espn_event("Australian Grand Prix", [
                _espn_competition("999", "Race", "2026-03-08T04:00Z", "in", [_espn_competitor(1, "Some Unknown Driver")]),
            ]),
        ]}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        cached_body = json.loads(s3.put_object.call_args.kwargs["Body"])
        assert cached_body["events"]["2026-2"]["participants"] == {}
        assert result == {"polled": 1}

    def test_a_practice_or_qualifying_session_is_never_cached_at_all(self):
        roster_event = _field_event("2026-1", "2026-03-01", [{"entity_id": "max_verstappen"}])
        storage = self._storage(roster_event)

        s3 = MagicMock()
        s3.get_object.side_effect = _NO_CACHE_YET
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": [
            _espn_event("Australian Grand Prix", [
                _espn_competition("111", "FP1", "2026-03-06T04:00Z", "in", [_espn_competitor(1, "Max Verstappen")]),
                _espn_competition("222", "Qual", "2026-03-07T04:00Z", "in", [_espn_competitor(1, "Max Verstappen")]),
            ]),
        ]}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        assert result == {"polled": 0}

    def test_a_session_with_no_matching_stored_event_is_skipped(self):
        roster_event = _field_event("2026-1", "2026-03-01", [{"entity_id": "max_verstappen"}])
        storage = self._storage(roster_event)  # no event on 2026-03-08 at all

        s3 = MagicMock()
        s3.get_object.side_effect = _NO_CACHE_YET
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": [
            _espn_event("Australian Grand Prix", [
                _espn_competition("999", "Race", "2026-03-08T04:00Z", "in", [_espn_competitor(1, "Max Verstappen")]),
            ]),
        ]}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        assert result == {"polled": 0}

    def test_a_just_finished_race_stays_cached_through_the_end_buffer_tail(self):
        roster_event = _field_event("2026-1", "2026-03-01", [{"entity_id": "max_verstappen"}])
        this_race = _field_event("2026-2", "2026-03-08", [], status="scheduled")
        storage = self._storage(roster_event, [this_race])

        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps({
                "fetched_at": recent, "events": {"2026-2": {"last_active_at": recent}},
            }).encode()),
        }
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": [
            _espn_event("Australian Grand Prix", [
                _espn_competition("999", "Race", "2026-03-08T04:00Z", "post", [_espn_competitor(1, "Max Verstappen", winner=True)]),
            ]),
        ]}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        assert result == {"polled": 1}

    def test_a_long_finished_race_past_the_end_buffer_is_no_longer_cached(self):
        roster_event = _field_event("2026-1", "2026-03-01", [{"entity_id": "max_verstappen"}])
        this_race = _field_event("2026-2", "2026-03-08", [], status="scheduled")
        storage = self._storage(roster_event, [this_race])

        long_ago = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps({
                "fetched_at": long_ago, "events": {"2026-2": {"last_active_at": long_ago}},
            }).encode()),
        }
        client = MagicMock()
        client.get_scoreboard.return_value = {"events": [
            _espn_event("Australian Grand Prix", [
                _espn_competition("999", "Race", "2026-03-08T04:00Z", "post", [_espn_competitor(1, "Max Verstappen", winner=True)]),
            ]),
        ]}

        result = live_scores.refresh(storage, s3, "bucket", client, "f1", 2026)

        assert result == {"polled": 0}


class TestGetLiveScores:
    def test_no_cache_returns_empty_events(self):
        s3 = MagicMock()
        s3.get_object.side_effect = _NO_CACHE_YET
        assert live_scores.get_live_scores(s3, "bucket") == {"events": {}}

    def test_fresh_cache_returns_its_events(self):
        now = datetime.now(timezone.utc).isoformat()
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps({"fetched_at": now, "events": {"2026-2": {"state": "in"}}}).encode()),
        }
        assert live_scores.get_live_scores(s3, "bucket") == {"events": {"2026-2": {"state": "in"}}}

    def test_stale_cache_returns_empty_events(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps({"fetched_at": old, "events": {"2026-2": {"state": "in"}}}).encode()),
        }
        assert live_scores.get_live_scores(s3, "bucket") == {"events": {}}
