"""
Unit tests for the NFL feature-engineering entrypoint's orchestration
logic -- grouping, history-filtering, and Parquet assembly. The actual
feature math is tested in tests/library/features/test_nfl.py; FeatureStorage
is mocked here so these tests only cover build_dataset.py's own wiring.
"""
import io
import json
from unittest.mock import MagicMock

import pandas as pd

import build_dataset


def _event(event_key, event_date, home_id, away_id, home_score=20, away_score=17):
    return {
        "event_key": event_key,
        "event_date": event_date,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


class TestGroupEventsByTeam:
    def test_groups_each_participant_and_sorts_ascending(self):
        events = [
            _event("E2", "2025-09-14", "KC", "LAC"),
            _event("E1", "2025-09-07", "KC", "DET"),
        ]

        grouped = build_dataset._group_events_by_team(events)

        assert [e["event_key"] for e in grouped["KC"]] == ["E1", "E2"]
        assert [e["event_key"] for e in grouped["LAC"]] == ["E2"]
        assert [e["event_key"] for e in grouped["DET"]] == ["E1"]


class TestHistoryBefore:
    def test_returns_prior_rows_most_recent_first(self):
        sorted_ascending = [
            {"event_date": "2025-09-07"},
            {"event_date": "2025-09-14"},
            {"event_date": "2025-09-21"},
        ]

        result = build_dataset._history_before(sorted_ascending, "2025-09-21")

        assert [r["event_date"] for r in result] == ["2025-09-14", "2025-09-07"]

    def test_empty_when_no_prior_rows(self):
        assert build_dataset._history_before([{"event_date": "2025-09-21"}], "2025-09-07") == []


class TestWriteParquet:
    def test_empty_rows_returns_empty_bytes(self):
        assert build_dataset._write_parquet([]) == b""

    def test_union_of_keys_used_as_columns_with_nan_for_missing(self):
        rows = [{"a": 1, "b": 2}, {"a": 3}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert list(df.columns) == ["a", "b"]
        assert df.iloc[0]["b"] == 2
        assert pd.isna(df.iloc[1]["b"])

    def test_dict_values_are_json_encoded(self):
        rows = [{"stat_line": {"passing_yards": 300}}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert json.loads(df.iloc[0]["stat_line"]) == {"passing_yards": 300}


class TestBuildEventDataset:
    def test_builds_one_row_per_event_and_skips_malformed(self):
        malformed = {
            "event_key": "BAD",
            "event_date": "2025-09-01",
            "participants": [{"entity_id": "KC", "role": "home", "result": {"score": 10}}],
        }
        events = [
            _event("E1", "2025-09-07", "KC", "LAC"),
            _event("E2", "2025-09-14", "KC", "DET"),
            malformed,
        ]
        storage = MagicMock()
        storage.get_all_events.return_value = events

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E1", "E2"}
        storage.get_all_events.assert_called_once_with(build_dataset.SPORT)

    def test_second_game_sees_first_games_pre_game_elo_change(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", home_score=27, away_score=20),
            _event("E2", "2025-09-14", "KC", "DET"),
        ]
        storage = MagicMock()
        storage.get_all_events.return_value = events

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_elo"] > 1500  # KC won E1 as home favorite -> rating rose


class TestBuildPlayerDataset:
    def test_builds_one_row_per_player_game_using_prior_games_only(self):
        games = [
            {
                "event_key": "E1", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "KC",
                "event_date": "2025-09-07", "stat_line": {"passing_yards": 250}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "KC",
                "event_date": "2025-09-14", "stat_line": {"passing_yards": 300}, "started": True,
            },
        ]
        storage = MagicMock()
        storage.get_all_player_game_stats.return_value = games

        rows = build_dataset.build_player_dataset(storage, window=5)

        first_row = next(r for r in rows if r["event_key"] == "E1")
        second_row = next(r for r in rows if r["event_key"] == "E2")
        assert "avg_passing_yards" not in first_row  # no prior games yet
        assert second_row["avg_passing_yards"] == 250  # only E1 counts as history
