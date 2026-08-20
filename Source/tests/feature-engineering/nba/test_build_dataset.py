"""
Unit tests for the NBA feature-engineering entrypoint's orchestration
logic -- grouping, history-filtering, and Parquet assembly. The actual
feature math is tested in tests/library/features/test_nba.py; FeatureStorage
is mocked here so these tests only cover build_dataset.py's own wiring.
Uses real NBA team ids (BOS=2, LAL=13, NY=18, PHI=20, TOR=28) since
build_event_dataset/build_player_dataset filter through
is_real_franchise_matchup.
"""
import io
import json
from unittest.mock import MagicMock

import pandas as pd

import build_dataset


def _event(event_key, event_date, home_id, away_id, home_score=110, away_score=105):
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
        rows = [{"stat_line": {"points": 30}}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert json.loads(df.iloc[0]["stat_line"]) == {"points": 30}


class TestBuildEventDataset:
    def _storage(self, events, team_game_stats=None):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        storage.get_all_team_game_stats.return_value = team_game_stats or []
        return storage

    def test_builds_one_row_per_event_and_skips_malformed(self):
        malformed = {
            "event_key": "BAD", "event_date": "2025-12-01",
            "participants": [{"entity_id": "2", "role": "home", "result": {"score": 100}}],
        }
        events = [
            _event("E1", "2025-12-01", "2", "13"),
            _event("E2", "2025-12-08", "2", "18"),
            malformed,
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E1", "E2"}
        storage.get_all_events.assert_called_once_with(build_dataset.SPORT)

    def test_excludes_non_franchise_participants(self):
        # e.g. an All-Star Game roster id -- is_real_franchise_matchup
        # excludes it before build_event_features ever sees it.
        events = [_event("E1", "2025-12-01", "2", "9001")]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert rows == []

    def test_second_game_sees_first_games_pre_game_elo_change(self):
        events = [
            _event("E1", "2025-12-01", "2", "13", home_score=120, away_score=100),
            _event("E2", "2025-12-08", "2", "18"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_elo"] > 1500  # BOS won E1 as home favorite -> rating rose

    def test_team_history_stays_capped_at_window_across_many_games(self):
        # BOS (2) plays 7 games against 7 different real opponents; window=3
        # means game 7 should only see the 3 most recent prior games.
        opponents = ["13", "18", "20", "28", "9", "12", "16"]
        events = [
            _event(f"E{i}", f"2025-12-{i:02d}", "2", opponents[i - 1])
            for i in range(1, 8)
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=3)

        e7 = next(row for row in rows if row["event_key"] == "E7")
        assert e7["home_games_played"] == 3

    def _team_box_row(self, event_key, team_id, points_scored_stat=None, turnovers=13, offensive_rebounds=32, defensive_rebounds=12):
        # No raw "rebounds" stat -- ESPN's team boxscore only exposes
        # offensive/defensive rebounds separately (see
        # library.features.nba._total_rebounds).
        return {
            "event_key": event_key, "team_key": f"nba:team:{team_id}", "team_id": team_id,
            "event_date": "2025-12-01", "stat_line": {
                "turnovers": turnovers,
                "offensive_rebounds": offensive_rebounds, "defensive_rebounds": defensive_rebounds,
            },
        }

    def test_team_box_stats_history_carries_forward_across_games(self):
        events = [
            _event("E1", "2025-12-01", "2", "13"),
            _event("E2", "2025-12-08", "2", "18"),
        ]
        team_game_stats = [
            self._team_box_row("E1", "2", turnovers=10, offensive_rebounds=35, defensive_rebounds=10),
            self._team_box_row("E1", "13", turnovers=14, offensive_rebounds=30, defensive_rebounds=10),
            self._team_box_row("E2", "2", turnovers=8, offensive_rebounds=38, defensive_rebounds=12),
            self._team_box_row("E2", "18", turnovers=12, offensive_rebounds=30, defensive_rebounds=12),
        ]
        storage = self._storage(events, team_game_stats=team_game_stats)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_box_games_played"] == 1
        assert e2["home_avg_turnovers"] == 10  # BOS's own E1 line
        assert e2["home_avg_rebounds"] == 45

    def test_missing_team_game_stats_row_yields_empty_box_history_not_an_error(self):
        events = [_event("E1", "2025-12-01", "2", "13")]
        storage = self._storage(events, team_game_stats=[])

        rows = build_dataset.build_event_dataset(storage, window=5)

        e1 = next(row for row in rows if row["event_key"] == "E1")
        assert e1["home_box_games_played"] == 0
        assert e1["home_avg_turnovers"] is None


class TestBuildPlayerDataset:
    def _storage(self, events, player_games):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        storage.get_all_player_game_stats.return_value = player_games
        return storage

    def test_builds_one_row_per_player_game_using_prior_games_only(self):
        games = [
            {
                "event_key": "E1", "player_key": "nba:player:p1", "entity_id": "p1", "team_id": "2",
                "event_date": "2025-12-01", "stat_line": {"points": 25}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "nba:player:p1", "entity_id": "p1", "team_id": "2",
                "event_date": "2025-12-08", "stat_line": {"points": 30}, "started": True,
            },
        ]
        events = [_event("E1", "2025-12-01", "2", "13"), _event("E2", "2025-12-08", "2", "18")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        first_row = next(r for r in rows if r["event_key"] == "E1")
        second_row = next(r for r in rows if r["event_key"] == "E2")
        assert "avg_points" not in first_row  # no prior games yet
        assert second_row["avg_points"] == 25  # only E1 counts as history

    def test_player_history_stays_capped_at_window_across_many_games(self):
        games = [
            {
                "event_key": f"E{i}", "player_key": "nba:player:p1", "entity_id": "p1", "team_id": "2",
                "event_date": f"2025-12-{i:02d}", "stat_line": {"points": 25}, "started": True,
            }
            for i in range(1, 8)
        ]
        events = [_event(f"E{i}", f"2025-12-{i:02d}", "2", "13") for i in range(1, 8)]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=3)

        e7 = next(r for r in rows if r["event_key"] == "E7")
        assert e7["games_played"] == 3

    def test_skips_a_player_game_whose_event_is_missing_or_malformed(self):
        games = [
            {
                "event_key": "MISSING", "player_key": "nba:player:p1", "entity_id": "p1", "team_id": "2",
                "event_date": "2025-12-01", "stat_line": {"points": 25}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "nba:player:p1", "entity_id": "p1", "team_id": "2",
                "event_date": "2025-12-08", "stat_line": {"points": 30}, "started": True,
            },
        ]
        events = [_event("E2", "2025-12-08", "2", "13")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E2"}

    def test_own_home_away_elo_and_rest_days_flow_through(self):
        games = [
            {
                "event_key": "E1", "player_key": "nba:player:p1", "entity_id": "p1", "team_id": "13",
                "event_date": "2025-12-01", "stat_line": {"points": 25}, "started": True,
            },
        ]
        # p1's team (LAL) is the away side of E1.
        events = [_event("E1", "2025-12-01", "2", "13")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        row = rows[0]
        assert row["is_home"] is False
        assert row["opponent_id"] == "2"
        assert row["own_elo"] is not None
        assert row["opponent_elo"] is not None
        assert row["rest_days"] is None  # no prior LAL event in this fixture
