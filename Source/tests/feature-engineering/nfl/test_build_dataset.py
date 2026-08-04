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
    def _storage(self, events, player_games=None, team_game_stats=None):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        storage.get_all_player_game_stats.return_value = player_games or []
        storage.get_all_team_game_stats.return_value = team_game_stats or []
        return storage

    def test_builds_one_row_per_event_and_skips_malformed(self):
        malformed = {
            "event_key": "BAD",
            "event_date": "2025-09-01",
            "participants": [{"entity_id": "12", "role": "home", "result": {"score": 10}}],
        }
        events = [
            _event("E1", "2025-09-07", "12", "24"),
            _event("E2", "2025-09-14", "12", "8"),
            malformed,
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E1", "E2"}
        storage.get_all_events.assert_called_once_with(build_dataset.SPORT)

    def test_second_game_sees_first_games_pre_game_elo_change(self):
        events = [
            _event("E1", "2025-09-07", "12", "24", home_score=27, away_score=20),
            _event("E2", "2025-09-14", "12", "8"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_elo"] > 1500  # KC won E1 as home favorite -> rating rose

    def test_team_history_stays_capped_at_window_across_many_games(self):
        # KC (12) plays 7 games against 7 different real opponents (so KC
        # is the only shared team); window=3 means game 7 should only see
        # the 3 most recent prior games, not all 6 -- the case the
        # incremental-history rewrite (replacing a full per-game history
        # re-filter) needs to get right. Opponent identities don't matter
        # for what this test checks, just that each is a real franchise
        # (is_real_franchise_matchup would otherwise exclude the event).
        opponents = ["13", "24", "25", "26", "27", "28", "29"]
        events = [
            _event(f"E{i}", f"2025-09-{i:02d}", "12", opponents[i - 1])
            for i in range(1, 8)
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=3)

        e7 = next(row for row in rows if row["event_key"] == "E7")
        assert e7["home_games_played"] == 3

    def _qb_game(self, event_key, team_id, event_date, entity_id="mahomes", passing_yards=300, passing_attempts=30):
        # "passing_yards", matching what normalize.py actually produces for
        # ESPN's passing category (see library/features/nfl.py's
        # build_event_features comment).
        return {
            "event_key": event_key, "player_key": f"PLAYER#{entity_id}", "entity_id": entity_id,
            "team_id": team_id, "event_date": event_date,
            "stat_line": {"passing_attempts": passing_attempts, "passing_yards": passing_yards},
        }

    def test_qb_history_is_empty_on_a_qbs_first_identified_start(self):
        events = [_event("E1", "2025-09-07", "12", "24")]
        player_games = [
            self._qb_game("E1", "12", "2025-09-07"),
            self._qb_game("E1", "24", "2025-09-07", entity_id="herbert", passing_yards=250),
        ]
        storage = self._storage(events, player_games)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e1 = next(row for row in rows if row["event_key"] == "E1")
        assert e1["home_qb_games_played"] == 0
        assert e1["home_qb_avg_passing_yards"] is None

    def test_qb_history_carries_forward_across_games_for_the_same_qb(self):
        events = [
            _event("E1", "2025-09-07", "12", "24"),
            _event("E2", "2025-09-14", "12", "8"),
        ]
        player_games = [
            self._qb_game("E1", "12", "2025-09-07", passing_yards=300),
            self._qb_game("E1", "24", "2025-09-07", entity_id="herbert"),
            self._qb_game("E2", "12", "2025-09-14", passing_yards=250),
            self._qb_game("E2", "8", "2025-09-14", entity_id="goff"),
        ]
        storage = self._storage(events, player_games)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_qb_games_played"] == 1
        assert e2["home_qb_avg_passing_yards"] == 300  # KC's QB's own E1 line, not the team's

    def test_no_identifiable_starter_yields_empty_qb_history_not_an_error(self):
        events = [_event("E1", "2025-09-07", "12", "24")]
        # No passing_attempts on either side's stat_line -- identify_starting_qb returns None.
        player_games = [
            {"event_key": "E1", "player_key": "PLAYER#k1", "entity_id": "k1", "team_id": "12",
             "event_date": "2025-09-07", "stat_line": {"field_goals_made": 2}},
        ]
        storage = self._storage(events, player_games)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e1 = next(row for row in rows if row["event_key"] == "E1")
        assert e1["home_qb_games_played"] == 0
        assert e1["away_qb_games_played"] == 0

    def _rb_game(self, event_key, team_id, event_date, entity_id="mccaffrey", rushing_yards=95, rushing_attempts=20):
        return {
            "event_key": event_key, "player_key": f"PLAYER#{entity_id}", "entity_id": entity_id,
            "team_id": team_id, "event_date": event_date,
            "stat_line": {"rushing_attempts": rushing_attempts, "rushing_yards": rushing_yards},
        }

    def _wr_game(self, event_key, team_id, event_date, entity_id="hill", receiving_yards=110, receiving_targets=9):
        return {
            "event_key": event_key, "player_key": f"PLAYER#{entity_id}", "entity_id": entity_id,
            "team_id": team_id, "event_date": event_date,
            "stat_line": {"receiving_targets": receiving_targets, "receiving_yards": receiving_yards},
        }

    def test_rb_and_wr_history_carry_forward_across_games(self):
        events = [
            _event("E1", "2025-09-07", "12", "24"),
            _event("E2", "2025-09-14", "12", "8"),
        ]
        player_games = [
            self._rb_game("E1", "12", "2025-09-07", rushing_yards=95),
            self._rb_game("E1", "24", "2025-09-07", entity_id="ekeler"),
            self._rb_game("E2", "12", "2025-09-14", rushing_yards=60),
            self._rb_game("E2", "8", "2025-09-14", entity_id="gibbs"),
            self._wr_game("E1", "12", "2025-09-07", receiving_yards=110),
            self._wr_game("E1", "24", "2025-09-07", entity_id="williams"),
            self._wr_game("E2", "12", "2025-09-14", receiving_yards=80),
            self._wr_game("E2", "8", "2025-09-14", entity_id="stbrown"),
        ]
        storage = self._storage(events, player_games)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_rb_games_played"] == 1
        assert e2["home_rb_avg_rushing_yards"] == 95  # KC's RB's own E1 line, not the team's
        assert e2["home_wr_games_played"] == 1
        assert e2["home_wr_avg_receiving_yards"] == 110

    def _team_box_row(self, event_key, team_id, turnovers=1, total_yards=350):
        return {
            "event_key": event_key, "team_key": f"TEAM#{team_id}", "team_id": team_id,
            "event_date": "2025-09-07", "stat_line": {"turnovers": turnovers, "total_yards": total_yards},
        }

    def test_team_box_stats_history_carries_forward_across_games(self):
        events = [
            _event("E1", "2025-09-07", "12", "24"),
            _event("E2", "2025-09-14", "12", "8"),
        ]
        team_game_stats = [
            self._team_box_row("E1", "12", turnovers=1, total_yards=350),
            self._team_box_row("E1", "24", turnovers=2, total_yards=280),
            self._team_box_row("E2", "12", turnovers=0, total_yards=400),
            self._team_box_row("E2", "8", turnovers=1, total_yards=300),
        ]
        storage = self._storage(events, team_game_stats=team_game_stats)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_box_games_played"] == 1
        assert e2["home_avg_turnovers"] == 1  # KC's own E1 line
        assert e2["home_avg_total_yards"] == 350

    def test_missing_team_game_stats_row_yields_empty_box_history_not_an_error(self):
        events = [_event("E1", "2025-09-07", "12", "24")]
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
                "event_key": "E1", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "12",
                "event_date": "2025-09-07", "stat_line": {"passing_yards": 250}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "12",
                "event_date": "2025-09-14", "stat_line": {"passing_yards": 300}, "started": True,
            },
        ]
        events = [_event("E1", "2025-09-07", "12", "24"), _event("E2", "2025-09-14", "12", "7")]
        storage = self._storage(events, games)

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
                "event_key": f"E{i}", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "12",
                "event_date": f"2025-09-{i:02d}", "stat_line": {"passing_yards": 250}, "started": True,
            }
            for i in range(1, 8)
        ]
        events = [_event(f"E{i}", f"2025-09-{i:02d}", "12", "24") for i in range(1, 8)]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=3)

        e7 = next(r for r in rows if r["event_key"] == "E7")
        assert e7["games_played"] == 3

    def test_skips_a_player_game_whose_event_is_missing_or_malformed(self):
        games = [
            {
                "event_key": "MISSING", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "12",
                "event_date": "2025-09-07", "stat_line": {"passing_yards": 250}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "12",
                "event_date": "2025-09-14", "stat_line": {"passing_yards": 300}, "started": True,
            },
        ]
        # No event for "MISSING" at all -- the player-game row referencing
        # it should be skipped, not crash.
        events = [_event("E2", "2025-09-14", "12", "7")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E2"}

    def test_own_home_away_elo_and_rest_days_flow_through(self):
        games = [
            {
                "event_key": "E1", "player_key": "PLAYER#p1", "entity_id": "p1", "team_id": "24",
                "event_date": "2025-09-07", "stat_line": {"passing_yards": 250}, "started": True,
            },
        ]
        # p1's team (LAC) is the away side of E1 -- is_home should read False,
        # and own/opponent Elo should resolve to LAC's/KC's ratings, not KC's/LAC's.
        events = [_event("E1", "2025-09-07", "12", "24")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        row = rows[0]
        assert row["is_home"] is False
        assert row["opponent_id"] == "12"
        assert row["own_elo"] is not None
        assert row["opponent_elo"] is not None
        assert row["rest_days"] is None  # no prior LAC event in this fixture
