"""
Unit tests for the NCAAFB feature-engineering entrypoint's orchestration
logic -- grouping, history-filtering, team-coordinate resolution, and the
team-week ranking walk. The actual feature math is tested in
tests/library/features/test_ncaafb_*.py; FeatureStorage is mocked here so
these tests only cover build_dataset.py's own wiring.
"""
import io
import json
from unittest.mock import MagicMock

import pandas as pd

import build_dataset


def _event(event_key, event_date, home_id, away_id, home_score=20, away_score=17, season=2025, **extra):
    return {
        "event_key": event_key,
        "event_date": event_date,
        "season": season,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
        **extra,
    }


def _storage(events, player_games=None, team_game_stats=None, entities=None):
    storage = MagicMock()
    storage.get_all_events.return_value = events
    storage.get_all_player_game_stats.return_value = player_games or []
    storage.get_all_team_game_stats.return_value = team_game_stats or []
    storage.get_entity.side_effect = lambda sport, team_id, entity_type: (entities or {}).get(team_id)
    return storage


class TestWriteParquet:
    def test_empty_rows_returns_empty_bytes(self):
        assert build_dataset._write_parquet([]) == b""

    def test_union_of_keys_used_as_columns_with_nan_for_missing(self):
        rows = [{"a": 1, "b": 2}, {"a": 3}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert list(df.columns) == ["a", "b"]
        assert pd.isna(df.iloc[1]["b"])

    def test_dict_values_are_json_encoded(self):
        rows = [{"stat_line": {"passing_yards": 300}}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert json.loads(df.iloc[0]["stat_line"]) == {"passing_yards": 300}


class TestTeamCoordinates:
    def test_resolves_coordinates_from_entity_metadata(self):
        storage = _storage([], entities={"333": {"metadata": {"latitude": 33.2, "longitude": -87.5}}})

        coords = build_dataset._team_coordinates(storage, {"333"})

        assert coords == {"333": (33.2, -87.5)}

    def test_missing_entity_is_omitted_not_a_crash(self):
        storage = _storage([], entities={})

        coords = build_dataset._team_coordinates(storage, {"333"})

        assert coords == {}

    def test_incomplete_coordinates_are_omitted(self):
        storage = _storage([], entities={"333": {"metadata": {"latitude": 33.2, "longitude": None}}})

        coords = build_dataset._team_coordinates(storage, {"333"})

        assert coords == {}


class TestBuildEventDataset:
    def test_builds_one_row_per_event_and_skips_malformed(self):
        malformed = {"event_key": "BAD", "event_date": "2025-09-01", "participants": [
            {"entity_id": "333", "role": "home", "result": {"score": 10}},
        ]}
        events = [_event("E1", "2025-09-07", "333", "61"), _event("E2", "2025-09-14", "333", "9"), malformed]
        storage = _storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E1", "E2"}
        storage.get_all_events.assert_called_once_with(build_dataset.SPORT)

    def test_second_game_sees_first_games_pre_game_elo_change(self):
        events = [
            _event("E1", "2025-09-07", "333", "61", home_score=27, away_score=20),
            _event("E2", "2025-09-14", "333", "9"),
        ]
        storage = _storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_elo"] > 1500

    def _qb_game(self, event_key, team_id, event_date, entity_id="qb1", passing_yards=300, passing_attempts=30):
        return {
            "event_key": event_key, "player_key": f"PLAYER#{entity_id}", "entity_id": entity_id,
            "team_id": team_id, "event_date": event_date,
            "stat_line": {"passing_attempts": passing_attempts, "passing_yards": passing_yards},
        }

    def test_qb_history_carries_forward_across_games_for_the_same_qb(self):
        events = [
            _event("E1", "2025-09-07", "333", "61"),
            _event("E2", "2025-09-14", "333", "9"),
        ]
        player_games = [
            self._qb_game("E1", "333", "2025-09-07", passing_yards=300),
            self._qb_game("E1", "61", "2025-09-07", entity_id="qb2"),
            self._qb_game("E2", "333", "2025-09-14", passing_yards=250),
            self._qb_game("E2", "9", "2025-09-14", entity_id="qb3"),
        ]
        storage = _storage(events, player_games)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_qb_games_played"] == 1
        assert e2["home_qb_avg_passing_yards"] == 300

    def _team_box_row(self, event_key, team_id, turnovers=1, total_yards=350):
        return {
            "event_key": event_key, "team_key": f"TEAM#{team_id}", "team_id": team_id,
            "event_date": "2025-09-07", "stat_line": {"turnovers": turnovers, "total_yards": total_yards},
        }

    def test_team_box_stats_history_carries_forward_across_games(self):
        events = [
            _event("E1", "2025-09-07", "333", "61"),
            _event("E2", "2025-09-14", "333", "9"),
        ]
        team_game_stats = [
            self._team_box_row("E1", "333", turnovers=1, total_yards=350),
            self._team_box_row("E2", "333", turnovers=0, total_yards=400),
        ]
        storage = _storage(events, team_game_stats=team_game_stats)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_box_games_played"] == 1
        assert e2["home_avg_turnovers"] == 1

    def test_team_coordinates_flow_into_travel_distance(self):
        events = [_event("E1", "2025-09-07", "333", "61")]
        storage = _storage(events, entities={
            "333": {"metadata": {"latitude": 33.2083, "longitude": -87.5504}},
            "61": {"metadata": {"latitude": 33.9498, "longitude": -83.3733}},
        })

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert rows[0]["home_travel_km"] == 0
        assert rows[0]["away_travel_km"] > 0


class TestBuildPlayerDataset:
    def test_builds_one_row_per_player_game_using_prior_games_only(self):
        games = [
            {"event_key": "E1", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "333",
             "event_date": "2025-09-07", "stat_line": {"passing_yards": 250}, "started": True},
            {"event_key": "E2", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "333",
             "event_date": "2025-09-14", "stat_line": {"passing_yards": 300}, "started": True},
        ]
        events = [_event("E1", "2025-09-07", "333", "61"), _event("E2", "2025-09-14", "333", "7")]
        storage = _storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        first_row = next(r for r in rows if r["event_key"] == "E1")
        second_row = next(r for r in rows if r["event_key"] == "E2")
        assert "avg_passing_yards" not in first_row
        assert second_row["avg_passing_yards"] == 250

    def test_skips_a_player_game_whose_event_is_missing(self):
        games = [
            {"event_key": "MISSING", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "333",
             "event_date": "2025-09-07", "stat_line": {"passing_yards": 250}, "started": True},
            {"event_key": "E2", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "333",
             "event_date": "2025-09-14", "stat_line": {"passing_yards": 300}, "started": True},
        ]
        events = [_event("E2", "2025-09-14", "333", "7")]
        storage = _storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E2"}


class TestBuildRankingDataset:
    def test_emits_a_row_per_team_per_event(self):
        events = [_event("E1", "2025-09-07", "333", "61")]
        storage = _storage(events)

        rows = build_dataset.build_ranking_dataset(storage)

        assert {row["team_id"] for row in rows} == {"333", "61"}

    def test_season_history_carries_forward_across_games_for_the_same_team(self):
        events = [
            _event("E1", "2025-09-07", "333", "61", home_score=30, away_score=10),
            _event("E2", "2025-09-14", "333", "9"),
        ]
        storage = _storage(events)

        rows = build_dataset.build_ranking_dataset(storage)

        e2_row = next(r for r in rows if r["event_key"] == "E2" and r["team_id"] == "333")
        assert e2_row["games_played"] == 1
        assert e2_row["wins"] == 1

    def test_season_boundary_resets_history(self):
        # Same team, different season -- E2's own row must not see E1's
        # game as season-to-date history.
        events = [
            _event("E1", "2025-09-07", "333", "61", season=2024),
            _event("E2", "2025-09-06", "333", "9", season=2025),
        ]
        storage = _storage(events)

        rows = build_dataset.build_ranking_dataset(storage)

        e2_row = next(r for r in rows if r["event_key"] == "E2" and r["team_id"] == "333")
        assert e2_row["games_played"] == 0

    def test_label_current_rank_read_from_the_event(self):
        events = [_event("E1", "2025-09-07", "333", "61", home_current_rank=4)]
        storage = _storage(events)

        rows = build_dataset.build_ranking_dataset(storage)

        home_row = next(r for r in rows if r["team_id"] == "333")
        assert home_row["label_current_rank"] == 4
