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

    def test_team_history_stays_capped_at_window_across_many_games(self):
        # KC plays 7 games (alternating opponents so KC is the only shared
        # team); window=3 means game 7 should only see the 3 most recent
        # prior games, not all 6 -- the case the incremental-history
        # rewrite (replacing a full per-game history re-filter) needs to
        # get right.
        events = [
            _event(f"E{i}", f"2025-09-{i:02d}", "KC", f"OPP{i}")
            for i in range(1, 8)
        ]
        storage = MagicMock()
        storage.get_all_events.return_value = events

        rows = build_dataset.build_event_dataset(storage, window=3)

        e7 = next(row for row in rows if row["event_key"] == "E7")
        assert e7["home_games_played"] == 3


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

    def test_player_history_stays_capped_at_window_across_many_games(self):
        # Same case as the event-dataset equivalent, for the per-player
        # incremental-history path.
        games = [
            {
                "event_key": f"E{i}", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "KC",
                "event_date": f"2025-09-{i:02d}", "stat_line": {"passing_yards": 250}, "started": True,
            }
            for i in range(1, 8)
        ]
        storage = MagicMock()
        storage.get_all_player_game_stats.return_value = games

        rows = build_dataset.build_player_dataset(storage, window=3)

        e7 = next(r for r in rows if r["event_key"] == "E7")
        assert e7["games_played"] == 3
