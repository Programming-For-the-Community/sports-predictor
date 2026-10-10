"""
Unit tests for the NHL feature-engineering entrypoint's own wiring --
what it reads, and that every dataset lands in S3 as readable Parquet.
The feature math and the chronological pass are tested in
tests/library/features/test_nhl.py and test_nhl_dataset.py; FeatureStorage
and S3 are mocked here. Uses real NHL team ids (BOS=1, NYR=13).
"""
import io
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import build_dataset

BOS, NYR = "1", "13"


def _event(event_id, day, home, away, home_periods, away_periods):
    def side(team_id, role, periods, other):
        return {"entity_id": team_id, "role": role, "result": {
            "score": sum(periods), "won": sum(periods) > sum(other), "period_scores": periods,
        }}
    return {
        "event_key": f"SPORT#NHL#EVENT#{event_id}", "event_date": day, "kickoff_time": f"{day}T23:00Z",
        "season": 2026, "season_type": 2, "went_to_overtime": False, "decided_by_shootout": False,
        "participants": [side(home, "home", home_periods, away_periods), side(away, "away", away_periods, home_periods)],
    }


def _goalie(event_id, day, team_id):
    return {
        "event_key": f"SPORT#NHL#EVENT#{event_id}", "player_key": f"SPORT#NHL#PLAYER#g-{team_id}",
        "entity_id": f"g-{team_id}", "team_id": team_id, "event_date": day, "position_group": "goalie", "started": True,
        "stat_line": {"saves": 27, "shots_against": 30, "goals_against": 3, "time_on_ice_seconds": 3600},
    }


def _skater(event_id, day, team_id):
    return {
        "event_key": f"SPORT#NHL#EVENT#{event_id}", "player_key": f"SPORT#NHL#PLAYER#s-{team_id}",
        "entity_id": f"s-{team_id}", "team_id": team_id, "event_date": day, "position_group": "skater", "position": "C",
        "stat_line": {"time_on_ice_seconds": 900, "shots_total": 3, "goals": 1, "assists": 0, "points": 1},
    }


def _storage():
    storage = MagicMock()
    storage.get_all_events.return_value = [
        _event("2", "2026-01-12", BOS, NYR, [1, 0, 0], [0, 0, 0]), _event("1", "2026-01-10", NYR, BOS, [2, 1, 0], [0, 1, 0]),
    ]
    storage.get_all_team_game_stats.return_value = []
    storage.get_all_player_game_stats.return_value = [
        build(event_id, day, team)
        for event_id, day in (("1", "2026-01-10"), ("2", "2026-01-12")) for team in (NYR, BOS) for build in (_goalie, _skater)
    ]
    return storage


class TestBuild:
    def test_reads_all_three_tables_for_nhl_with_the_lookback(self):
        storage = _storage()

        build_dataset.build(storage, since_date="2016-01-01")

        storage.get_all_events.assert_called_once_with("nhl", since_date="2016-01-01")
        storage.get_all_team_game_stats.assert_called_once_with("nhl", since_date="2016-01-01")
        storage.get_all_player_game_stats.assert_called_once_with("nhl", since_date="2016-01-01")

    def test_returns_event_rows_and_a_row_per_starting_goalie_and_per_skater(self):
        event_rows, goalie_rows, player_rows = build_dataset.build(_storage())

        assert [row["event_date"] for row in event_rows] == ["2026-01-10", "2026-01-12"]
        assert len(goalie_rows) == 4
        assert len(player_rows) == 4


class TestMain:
    def _run(self, monkeypatch, storage):
        monkeypatch.setenv("MODEL_ARTIFACTS_BUCKET_NAME", "artifacts")
        monkeypatch.delenv("TRAINING_LOOKBACK_SEASONS", raising=False)
        s3 = MagicMock()
        s3.bucket = "artifacts"
        with patch.object(build_dataset, "FeatureStorage", return_value=storage), \
             patch.object(build_dataset, "S3Manager", return_value=s3):
            build_dataset.main()
        return {call.args[0]: call.args[1] for call in s3.put_bytes.call_args_list}

    def test_writes_all_three_datasets_as_parquet_to_their_own_keys(self, monkeypatch):
        written = self._run(monkeypatch, _storage())

        assert set(written) == {
            "nhl/training-data/event_features.parquet", "nhl/training-data/goalie_features.parquet",
            "nhl/training-data/player_features.parquet",
        }
        players = pd.read_parquet(io.BytesIO(written["nhl/training-data/player_features.parquet"]))
        assert len(players) == 4
        assert {"avg_shots_total", "games_with_shots_total", "shots_per_60", "opp_allowed_shots_total"} <= set(players.columns)
        assert json.loads(players.iloc[0]["label_stat_line"])["shots_total"] == 3
        events = pd.read_parquet(io.BytesIO(written["nhl/training-data/event_features.parquet"]))
        goalies = pd.read_parquet(io.BytesIO(written["nhl/training-data/goalie_features.parquet"]))
        assert len(events) == 2
        assert {"home_elo", "home_goals_for_last10", "away_goalie_save_pct_career", "label_home_won"} <= set(events.columns)
        assert events.iloc[1]["away_goals_for_last10"] == 3
        assert len(goalies) == 4
        assert json.loads(goalies.iloc[0]["label_stat_line"])["saves"] == 27

    def test_refuses_to_overwrite_a_dataset_with_nothing(self, monkeypatch):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        storage.get_all_team_game_stats.return_value = []
        storage.get_all_player_game_stats.return_value = []

        with pytest.raises(RuntimeError, match="0 rows"):
            self._run(monkeypatch, storage)
