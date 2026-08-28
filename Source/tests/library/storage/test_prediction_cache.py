"""
Unit tests for library.storage.prediction_cache -- the shared S3-backed
on-demand prediction cache used by both NFL and NCAAFB's predict/
predict-read Lambdas. s3 is a MagicMock throughout, same convention as
test_season_projections.py/test_model_artifacts.py.
"""
import time
from unittest.mock import MagicMock

from library.storage import prediction_cache


class TestCacheKeys:
    def test_event_prediction_cache_key(self):
        assert prediction_cache.event_prediction_cache_key("nfl", "SPORT#NFL#EVENT#1") == "predictions-cache/nfl/events/SPORT#NFL#EVENT#1.json"

    def test_player_prop_cache_key(self):
        key = prediction_cache.player_prop_cache_key("ncaafb", "SPORT#NCAAFB#EVENT#1", "qb1", "passing_yards")
        assert key == "predictions-cache/ncaafb/events/SPORT#NCAAFB#EVENT#1/players/qb1/passing_yards.json"

    def test_player_prop_model_name(self):
        assert prediction_cache.player_prop_model_name("passing_yards") == "player-prop-passing-yards"


class TestGetCached:
    def test_returns_none_when_nothing_cached(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert prediction_cache.get_cached(s3, "predictions-cache/nfl/events/E1.json") is None

    def test_returns_the_cached_envelope(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"model_versions": {"win_probability": 1}, "result": {"foo": "bar"}}

        entry = prediction_cache.get_cached(s3, "predictions-cache/nfl/events/E1.json")

        assert entry["result"] == {"foo": "bar"}


class TestIsFresh:
    def test_stale_when_model_versions_differ(self):
        entry = {"model_versions": {"win_probability": 1}, "event_status": "completed", "cached_at_epoch": time.time()}

        assert prediction_cache.is_fresh(entry, {"win_probability": 2}) is False

    def test_completed_event_never_expires_once_versions_match(self):
        entry = {"model_versions": {"win_probability": 1}, "event_status": "completed", "cached_at_epoch": time.time() - 999999}

        assert prediction_cache.is_fresh(entry, {"win_probability": 1}) is True

    def test_scheduled_event_expires_after_the_ttl(self):
        entry = {"model_versions": {"win_probability": 1}, "event_status": "scheduled", "cached_at_epoch": time.time() - (prediction_cache.STALE_AFTER_SECONDS + 60)}

        assert prediction_cache.is_fresh(entry, {"win_probability": 1}) is False

    def test_scheduled_event_is_fresh_within_the_ttl(self):
        entry = {"model_versions": {"win_probability": 1}, "event_status": "scheduled", "cached_at_epoch": time.time() - 10}

        assert prediction_cache.is_fresh(entry, {"win_probability": 1}) is True

    def test_extra_fingerprint_defaults_to_unused(self):
        # No caller not passing it should see any behavior change.
        entry = {"model_versions": {"win_probability": 1}, "event_status": "scheduled", "cached_at_epoch": time.time() - 10}

        assert prediction_cache.is_fresh(entry, {"win_probability": 1}) is True

    def test_stale_when_extra_fingerprint_differs(self):
        entry = {
            "model_versions": {"top_10_probability": 1}, "event_status": "scheduled",
            "cached_at_epoch": time.time() - 10, "extra_fingerprint": 3,
        }

        assert prediction_cache.is_fresh(entry, {"top_10_probability": 1}, extra_fingerprint=4) is False

    def test_fresh_when_extra_fingerprint_matches(self):
        entry = {
            "model_versions": {"top_10_probability": 1}, "event_status": "scheduled",
            "cached_at_epoch": time.time() - 10, "extra_fingerprint": 3,
        }

        assert prediction_cache.is_fresh(entry, {"top_10_probability": 1}, extra_fingerprint=3) is True

    def test_stale_when_extra_fingerprint_is_missing_from_an_old_style_entry(self):
        # An entry cached before extra_fingerprint tracking existed --
        # forces one recompute to pick up the new tracking.
        entry = {"model_versions": {"top_10_probability": 1}, "event_status": "scheduled", "cached_at_epoch": time.time() - 10}

        assert prediction_cache.is_fresh(entry, {"top_10_probability": 1}, extra_fingerprint=0) is False


class TestPutCached:
    def test_writes_the_expected_envelope_shape(self):
        s3 = MagicMock()

        prediction_cache.put_cached(s3, "predictions-cache/nfl/events/E1.json", {"foo": "bar"}, {"win_probability": 1}, "scheduled")

        key, payload = s3.put_json.call_args[0]
        assert key == "predictions-cache/nfl/events/E1.json"
        assert payload["result"] == {"foo": "bar"}
        assert payload["model_versions"] == {"win_probability": 1}
        assert payload["event_status"] == "scheduled"
        assert payload["extra_fingerprint"] is None
        assert "cached_at_epoch" in payload

    def test_records_a_real_extra_fingerprint_when_given_one(self):
        s3 = MagicMock()

        prediction_cache.put_cached(s3, "predictions-cache/pga/events/E1.json", {"foo": "bar"}, {"top_10_probability": 1}, "scheduled", extra_fingerprint=5)

        _, payload = s3.put_json.call_args[0]
        assert payload["extra_fingerprint"] == 5


class TestCurrentModelVersions:
    """The generic function current_core_model_versions is now built on
    top of -- covers a non-head-to-head model-name map (e.g. PGA's own),
    so this module doesn't need a second hardcoded constant per sport
    shape."""

    def test_reads_a_caller_supplied_model_map(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"version": 5}

        versions = prediction_cache.current_model_versions(
            s3, "pga", {"top_10": "top-10-probability", "score": "projected-score-to-par"},
        )

        assert versions == {"top_10": 5, "score": 5}

    def test_an_unpromoted_model_in_the_map_reads_as_none(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        versions = prediction_cache.current_model_versions(s3, "pga", {"top_10": "top-10-probability"})

        assert versions == {"top_10": None}


class TestCurrentCoreModelVersions:
    def test_reads_all_four_pointers(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"version": 3}

        versions = prediction_cache.current_core_model_versions(s3, "nfl")

        assert versions == {"win_probability": 3, "margin": 3, "home_score": 3, "away_score": 3}

    def test_a_never_promoted_model_reads_as_none(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        versions = prediction_cache.current_core_model_versions(s3, "nfl")

        assert versions == {"win_probability": None, "margin": None, "home_score": None, "away_score": None}


class TestCurrentPlayerPropModelVersion:
    def test_returns_the_promoted_version(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"version": 5}

        assert prediction_cache.current_player_prop_model_version(s3, "ncaafb", "passing_yards") == 5

    def test_none_when_never_promoted(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert prediction_cache.current_player_prop_model_version(s3, "ncaafb", "passing_yards") is None


class TestInProgressClaim:
    def test_claims_when_nothing_in_progress(self):
        s3 = MagicMock()
        s3.object_exists.return_value = False

        assert prediction_cache.claim_in_progress(s3, "predictions-cache/nfl/events/E1.json") is True
        s3.put_json.assert_called_once()

    def test_does_not_claim_when_a_recent_claim_already_exists(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"started_at_epoch": time.time()}

        assert prediction_cache.claim_in_progress(s3, "predictions-cache/nfl/events/E1.json") is False

    def test_claims_again_once_the_previous_claim_has_expired(self):
        s3 = MagicMock()
        s3.object_exists.return_value = True
        s3.get_json.return_value = {"started_at_epoch": time.time() - (prediction_cache.IN_PROGRESS_TTL_SECONDS + 10)}

        assert prediction_cache.claim_in_progress(s3, "predictions-cache/nfl/events/E1.json") is True

    def test_clear_deletes_the_marker_object(self):
        s3 = MagicMock()

        prediction_cache.clear_in_progress(s3, "predictions-cache/nfl/events/E1.json")

        s3.delete_object.assert_called_once_with("predictions-cache/nfl/events/E1.json.in-progress")
