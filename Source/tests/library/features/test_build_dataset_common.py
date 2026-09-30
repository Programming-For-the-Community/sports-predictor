"""
Unit tests for library.features.build_dataset_common.write_dataset -- the
empty-dataset guard every sport's feature-engineering main() relies on.
"""
import inspect
import logging
from unittest.mock import MagicMock

import pytest

from library.features import build_dataset_common

_logger = logging.getLogger("test")


def _s3():
    s3 = MagicMock()
    s3.bucket = "models"
    return s3


class TestWriteDataset:
    def test_refuses_to_overwrite_with_an_empty_dataset(self):
        s3 = _s3()

        with pytest.raises(RuntimeError, match=r"player produced 0 rows -- refusing to overwrite s3://models/k\.parquet"):
            build_dataset_common.write_dataset(s3, "k.parquet", [], "player", _logger)
        s3.put_bytes.assert_not_called()

    def test_uploads_the_serialized_rows_and_clears_the_row_list(self):
        s3 = _s3()
        rows = [{"a": 1}, {"a": 2}]
        serialized = []

        def _serialize(rows_to_write):
            serialized.append(list(rows_to_write))
            return b"parquet-bytes"

        build_dataset_common.write_dataset(s3, "k.parquet", rows, "player", _logger, serialize=_serialize)

        assert serialized == [[{"a": 1}, {"a": 2}]]
        s3.put_bytes.assert_called_once_with("k.parquet", b"parquet-bytes", content_type="application/octet-stream")
        assert rows == []

    def test_serializes_to_parquet_by_default(self):
        default = inspect.signature(build_dataset_common.write_dataset).parameters["serialize"].default

        assert default is build_dataset_common.write_parquet

    def test_uses_a_custom_serializer_when_given(self):
        s3 = _s3()

        build_dataset_common.write_dataset(s3, "k", [{"a": 1}], "driver", _logger, serialize=lambda rows: b"custom")

        assert s3.put_bytes.call_args.args == ("k", b"custom")


def _event(event_key: str, event_date: str, home_id: str = "H", away_id: str = "A") -> dict:
    return {
        "event_key": event_key, "event_date": event_date,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"won": True, "score": 2}},
            {"entity_id": away_id, "role": "away", "result": {"won": False, "score": 1}},
        ],
    }


def _record_calls(calls: list):
    def build(event, elo_ratings, home_history, away_history, window, **kwargs):
        calls.append({"event": event["event_key"], "home": home_history, "away": away_history, **kwargs})
        return {"event_key": event["event_key"]}
    return build


class TestBuildTeamEventRows:
    def test_appends_only_the_box_score_rows_that_exist(self):
        calls = []
        events = [_event("E1", "2025-09-01"), _event("E2", "2025-09-08")]

        rows = build_dataset_common.build_team_event_rows(
            events, [{"event_key": "E1", "team_id": "A", "yds": 300}], 5, _record_calls(calls), logging.getLogger(),
        )

        assert rows == [{"event_key": "E1"}, {"event_key": "E2"}]
        assert calls[1]["home_team_box_stats"] == []
        assert calls[1]["away_team_box_stats"] == [{"event_key": "E1", "team_id": "A", "yds": 300}]

    def test_passes_no_position_games_without_leaders(self):
        calls = []

        build_dataset_common.build_team_event_rows(
            [_event("E1", "2025-09-01")], [], 5, _record_calls(calls), logging.getLogger(),
        )

        assert "home_position_games" not in calls[0]

    def test_tracks_each_leaders_own_prior_games_across_teams(self):
        calls = []
        events = [_event("E1", "2025-09-01", "H", "A"), _event("E2", "2025-09-08", "X", "H")]
        qb_e1 = {"event_key": "E1", "team_id": "H", "entity_id": "qb1"}
        qb_e2 = {"event_key": "E2", "team_id": "H", "entity_id": "qb1"}

        def first_or_none(games):
            return games[0] if games else None

        build_dataset_common.build_team_event_rows(
            events, [], 5, _record_calls(calls), logging.getLogger(),
            player_games=[qb_e1, qb_e2], leaders={"qb": first_or_none},
        )

        assert calls[0]["home_position_games"] == {"qb": []}
        assert calls[0]["away_position_games"] == {"qb": []}
        assert calls[1]["away_position_games"] == {"qb": [qb_e1]}
        assert calls[1]["home_position_games"] == {"qb": []}
