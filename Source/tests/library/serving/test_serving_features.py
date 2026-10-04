from unittest.mock import MagicMock

from library.serving import serving_features

CARD = {"model_name": "top-10-probability", "version": 3}


class TestCaptureKey:
    def test_a_participant_row_is_keyed_by_model_version_and_entity(self):
        assert serving_features.capture_key(CARD, {"entity_id": "42"}) == "top-10-probability#v3#42"

    def test_an_event_row_is_keyed_by_model_and_version(self):
        assert serving_features.capture_key(CARD, {"event_key": "SPORT#NFL#EVENT#1"}) == "top-10-probability#v3"


class TestCapture:
    def test_nothing_is_recorded_outside_a_capture(self):
        serving_features.note(CARD, {}, {"a": 1.0})

        with serving_features.capturing() as captured:
            pass

        assert captured == {}

    def test_inputs_are_recorded_with_nan_as_null(self):
        with serving_features.capturing() as captured:
            serving_features.note(CARD, {"entity_id": "1"}, {"a": 1.5, "b": float("nan")})

        assert captured == {"top-10-probability#v3#1": {"a": 1.5, "b": None}}


class TestWrite:
    def test_writes_one_file_per_event(self):
        s3 = MagicMock()

        serving_features.write(s3, "nfl", "SPORT#NFL#EVENT#401", {"win-probability#v2": {"a": 1.0}})

        s3.put_json.assert_called_once_with("serving-features/nfl/401.json", {"win-probability#v2": {"a": 1.0}})

    def test_nothing_captured_writes_nothing(self):
        s3 = MagicMock()

        serving_features.write(s3, "nfl", "SPORT#NFL#EVENT#401", {})

        s3.put_json.assert_not_called()

    def test_a_failed_write_is_logged_not_raised(self, caplog):
        s3 = MagicMock()
        s3.put_json.side_effect = RuntimeError("denied")

        serving_features.write(s3, "nfl", "SPORT#NFL#EVENT#401", {"k": {}})

        assert "Failed writing serving features" in caplog.text
