"""
Unit tests for the F1 normalize Lambda handler. All AWS calls are
mocked. Tests verify key routing via _dispatch (results/qualifying/
sprint all reach DynamoDB now; pitstops/standings stay recognized-but-
skipped raw-only prefixes), the results<->qualifying merge in both
trigger directions, that constructor and driver entities are upserted
before the event that references them, and that the handler is
resilient to individual record failures. The normalizer logic itself is
covered by tests/library/normalize/test_f1_event.py -- these tests
exercise the dispatch/wiring only.

The f1_normalize module is registered in sys.modules by conftest.py.
"""
import json

import pytest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import f1_normalize


@pytest.fixture(autouse=True)
def reset_storage():
    f1_normalize._storage = None
    yield
    f1_normalize._storage = None


def _s3_response(payload) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode()
    return {"Body": body}


def _s3_record(bucket: str, key: str) -> dict:
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}


def _no_such_key() -> ClientError:
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")


def _s3_with(responses: dict[str, dict]) -> MagicMock:
    """A mock S3 client whose get_object returns a different canned
    payload per exact key, raising NoSuchKey (via botocore's own real
    exception type) for any key not in `responses` -- lets a single test
    exercise both the triggering object and a real/missing companion
    file at once."""
    mock_s3 = MagicMock()

    def _get_object(Bucket, Key):
        if Key not in responses:
            raise _no_such_key()
        return _s3_response(responses[Key])

    mock_s3.get_object.side_effect = _get_object
    return mock_s3


def _results_payload(season="2024", round_="1"):
    return {
        "MRData": {
            "RaceTable": {
                "season": season, "round": round_,
                "Races": [{
                    "season": season, "round": round_, "raceName": "Bahrain Grand Prix",
                    "Circuit": {"circuitId": "bahrain", "circuitName": "Bahrain International Circuit", "Location": {"locality": "Sakhir", "country": "Bahrain"}},
                    "date": "2024-03-02",
                    "Results": [{
                        "position": "1", "positionText": "1", "points": "26",
                        "Driver": {"driverId": "max_verstappen", "givenName": "Max", "familyName": "Verstappen", "nationality": "Dutch"},
                        "Constructor": {"constructorId": "red_bull", "name": "Red Bull", "nationality": "Austrian"},
                        "grid": "1", "laps": "57", "status": "Finished",
                        "FastestLap": {"rank": "1"},
                    }],
                }],
            },
        },
    }


def _qualifying_payload(season="2024", round_="1"):
    return {
        "MRData": {
            "RaceTable": {
                "season": season, "round": round_,
                "Races": [{
                    "season": season, "round": round_,
                    "QualifyingResults": [{
                        "position": "1",
                        "Driver": {"driverId": "max_verstappen"}, "Constructor": {"constructorId": "red_bull"},
                        "Q1": "1:30.031", "Q2": "1:29.374", "Q3": "1:29.179",
                    }],
                }],
            },
        },
    }


def _sprint_payload(season="2024", round_="5", with_results=True):
    race = {
        "season": season, "round": round_, "raceName": "Chinese Grand Prix",
        "Circuit": {"circuitId": "shanghai", "circuitName": "Shanghai International Circuit", "Location": {"locality": "Shanghai", "country": "China"}},
        "date": "2024-04-21",
    }
    if with_results:
        race["SprintResults"] = [{
            "position": "1", "positionText": "1", "points": "8",
            "Driver": {"driverId": "max_verstappen", "givenName": "Max", "familyName": "Verstappen", "nationality": "Dutch"},
            "Constructor": {"constructorId": "red_bull", "name": "Red Bull", "nationality": "Austrian"},
            "grid": "1", "laps": "19", "status": "Finished",
        }]
    return {"MRData": {"RaceTable": {"season": season, "round": round_, "Races": [race]}}}


class TestResultsDispatch:
    def test_upserts_entities_and_event_when_no_qualifying_exists_yet(self):
        mock_s3 = _s3_with({"f1/results/2024/1.json": _results_payload()})
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/results/2024/1.json")]}, None)

        assert result == {"processed": 1, "failed": 0}
        assert mock_storage.upsert_event.call_count == 1
        assert mock_storage.upsert_entity.call_count == 2  # 1 driver + 1 constructor
        event_item = mock_storage.upsert_event.call_args.args[0]
        assert event_item["participants"][0]["result"]["qualifying"] is None

    def test_merges_qualifying_when_it_already_exists_in_s3(self):
        mock_s3 = _s3_with({
            "f1/results/2024/1.json": _results_payload(),
            "f1/qualifying/2024/1.json": _qualifying_payload(),
        })
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/results/2024/1.json")]}, None)

        event_item = mock_storage.upsert_event.call_args.args[0]
        assert event_item["participants"][0]["result"]["qualifying"]["position"] == 1

    def test_entities_are_upserted_before_the_event(self):
        mock_s3 = _s3_with({"f1/results/2024/1.json": _results_payload()})
        mock_storage = MagicMock()
        calls = []
        mock_storage.upsert_entity.side_effect = lambda item: calls.append(("entity", item))
        mock_storage.upsert_event.side_effect = lambda item: calls.append(("event", item))

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/results/2024/1.json")]}, None)

        assert [kind for kind, _ in calls] == ["entity", "entity", "event"]


class TestQualifyingDispatch:
    def test_merges_into_the_existing_results_event_and_upserts(self):
        mock_s3 = _s3_with({
            "f1/results/2024/1.json": _results_payload(),
            "f1/qualifying/2024/1.json": _qualifying_payload(),
        })
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/qualifying/2024/1.json")]}, None)

        assert result == {"processed": 1, "failed": 0}
        mock_storage.upsert_event.assert_called_once()
        event_item = mock_storage.upsert_event.call_args.args[0]
        assert event_item["participants"][0]["result"]["qualifying"]["position"] == 1
        # Entities aren't re-upserted on the qualifying-triggered pass --
        # results already wrote them.
        mock_storage.upsert_entity.assert_not_called()

    def test_deferred_without_error_when_results_has_not_been_ingested_yet(self):
        mock_s3 = _s3_with({"f1/qualifying/2024/1.json": _qualifying_payload()})  # no results.json
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/qualifying/2024/1.json")]}, None)

        assert result == {"processed": 1, "failed": 0}  # not an error -- just deferred
        mock_storage.upsert_event.assert_not_called()


class TestSprintDispatch:
    def test_a_real_sprint_weekend_upserts_a_distinct_sprint_event(self):
        mock_s3 = _s3_with({"f1/sprint/2024/5.json": _sprint_payload()})
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/sprint/2024/5.json")]}, None)

        assert result == {"processed": 1, "failed": 0}
        mock_storage.upsert_event.assert_called_once()
        event_item = mock_storage.upsert_event.call_args.args[0]
        assert event_item["event_type"] == "sprint"
        assert mock_storage.upsert_entity.call_count == 2

    def test_a_stray_sprint_file_with_no_real_results_is_skipped(self):
        mock_s3 = _s3_with({"f1/sprint/2024/1.json": _sprint_payload(round_="1", with_results=False)})
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/sprint/2024/1.json")]}, None)

        assert result == {"processed": 1, "failed": 0}
        mock_storage.upsert_event.assert_not_called()


class TestRawOnlyAndUnrecognizedKeys:
    @pytest.mark.parametrize("key", [
        "f1/pitstops/2024/1.json",
        "f1/standings/2024/20240302.json",
    ])
    def test_a_raw_only_prefix_is_recognized_and_skipped_not_fetched(self, key):
        mock_s3 = MagicMock()
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", key)]}, None)

        assert result == {"processed": 1, "failed": 0}
        mock_s3.get_object.assert_not_called()
        mock_storage.upsert_event.assert_not_called()

    def test_an_unrecognized_key_pattern_is_logged_and_skipped(self):
        mock_s3 = MagicMock()
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({"Records": [_s3_record("bucket", "f1/something-unexpected/2024.json")]}, None)

        assert result == {"processed": 1, "failed": 0}
        mock_storage.upsert_event.assert_not_called()


class TestMultiRecordResilience:
    def test_one_records_failure_does_not_block_the_others(self):
        # Record 1's own get_object raises outright (a real S3 error,
        # not a NoSuchKey). Record 2 succeeds on its own triggering read,
        # then makes one more get_object call for its qualifying
        # companion lookup, which genuinely doesn't exist -- NoSuchKey.
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = [Exception("s3 error"), _s3_response(_results_payload()), _no_such_key()]
        mock_storage = MagicMock()

        with patch.object(f1_normalize, "_s3", mock_s3), \
             patch.object(f1_normalize, "_get_storage", return_value=mock_storage):
            result = f1_normalize.lambda_handler({
                "Records": [
                    _s3_record("bucket", "f1/results/2024/1.json"),
                    _s3_record("bucket", "f1/results/2024/2.json"),
                ],
            }, None)

        assert result == {"processed": 1, "failed": 1}
