"""
Unit tests for the NCAA MBB feature-engineering entrypoint's orchestration
logic -- grouping, history-filtering, and Parquet assembly. The actual
feature math is tested in tests/library/features/test_ncaambb.py;
FeatureStorage is mocked here so these tests only cover build_dataset.py's
own wiring.

Unlike NBA's own test_build_dataset.py, there's no
is_real_franchise_matchup-equivalent test here -- build_event_dataset/
build_player_dataset don't filter participants at all (see build_dataset.py's
own docstring for why: no exhibition concept exists, and NIT games are
deliberately included, not filtered).

TestLoadRankings/TestTeamPreRating/TestResolveOwnElo/TestBuildRankingDataset
cover the National Ranking model's own poll-centric dataset builder --
see build_dataset.py's and library.features.ncaambb.build_team_week_features's
own docstrings for why it's shaped differently from NCAAFB's event-centric
equivalent.
"""
import io
import json
from unittest.mock import MagicMock

import pandas as pd

import build_dataset


def _event(event_key, event_date, home_id, away_id, home_score=80, away_score=75):
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
        rows = [{"stat_line": {"points": 22}}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert json.loads(df.iloc[0]["stat_line"]) == {"points": 22}


class TestBuildEventDataset:
    def _storage(self, events, team_game_stats=None):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        storage.get_all_team_game_stats.return_value = team_game_stats or []
        return storage

    def test_builds_one_row_per_event_and_skips_malformed(self):
        malformed = {
            "event_key": "BAD", "event_date": "2025-12-01",
            "participants": [{"entity_id": "150", "role": "home", "result": {"score": 80}}],
        }
        events = [
            _event("E1", "2025-12-01", "150", "153"),
            _event("E2", "2025-12-08", "150", "160"),
            malformed,
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E1", "E2"}
        storage.get_all_events.assert_called_once_with(build_dataset.SPORT)

    def test_second_game_sees_first_games_pre_game_elo_change(self):
        events = [
            _event("E1", "2025-12-01", "150", "153", home_score=90, away_score=70),
            _event("E2", "2025-12-08", "150", "160"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_elo"] > 1500  # team 150 won E1 as home favorite -> rating rose

    def test_team_history_stays_capped_at_window_across_many_games(self):
        opponents = ["153", "160", "170", "180", "190", "200", "210"]
        events = [
            _event(f"E{i}", f"2025-12-{i:02d}", "150", opponents[i - 1])
            for i in range(1, 8)
        ]
        storage = self._storage(events)

        rows = build_dataset.build_event_dataset(storage, window=3)

        e7 = next(row for row in rows if row["event_key"] == "E7")
        assert e7["home_games_played"] == 3

    def _team_box_row(self, event_key, team_id, rebounds=38, turnovers=13, offensive_rebounds=9, defensive_rebounds=29):
        # A real raw "rebounds" stat -- unlike NBA's box score, no
        # derivation-from-offense+defense workaround needed here.
        return {
            "event_key": event_key, "team_key": f"ncaambb:team:{team_id}", "team_id": team_id,
            "event_date": "2025-12-01", "stat_line": {
                "rebounds": rebounds, "turnovers": turnovers,
                "offensive_rebounds": offensive_rebounds, "defensive_rebounds": defensive_rebounds,
            },
        }

    def test_team_box_stats_history_carries_forward_across_games(self):
        events = [
            _event("E1", "2025-12-01", "150", "153"),
            _event("E2", "2025-12-08", "150", "160"),
        ]
        team_game_stats = [
            self._team_box_row("E1", "150", rebounds=40, turnovers=10),
            self._team_box_row("E1", "153", rebounds=35, turnovers=14),
            self._team_box_row("E2", "150", rebounds=38, turnovers=8),
            self._team_box_row("E2", "160", rebounds=33, turnovers=12),
        ]
        storage = self._storage(events, team_game_stats=team_game_stats)

        rows = build_dataset.build_event_dataset(storage, window=5)

        e2 = next(row for row in rows if row["event_key"] == "E2")
        assert e2["home_box_games_played"] == 1
        assert e2["home_avg_turnovers"] == 10  # team 150's own E1 line
        assert e2["home_avg_rebounds"] == 40  # read directly, not derived

    def test_missing_team_game_stats_row_yields_empty_box_history_not_an_error(self):
        events = [_event("E1", "2025-12-01", "150", "153")]
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
                "event_key": "E1", "player_key": "ncaambb:player:p1", "entity_id": "p1", "team_id": "150",
                "event_date": "2025-12-01", "stat_line": {"points": 20}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "ncaambb:player:p1", "entity_id": "p1", "team_id": "150",
                "event_date": "2025-12-08", "stat_line": {"points": 24}, "started": True,
            },
        ]
        events = [_event("E1", "2025-12-01", "150", "153"), _event("E2", "2025-12-08", "150", "160")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        first_row = next(r for r in rows if r["event_key"] == "E1")
        second_row = next(r for r in rows if r["event_key"] == "E2")
        assert "avg_points" not in first_row  # no prior games yet
        assert second_row["avg_points"] == 20  # only E1 counts as history

    def test_player_history_stays_capped_at_window_across_many_games(self):
        games = [
            {
                "event_key": f"E{i}", "player_key": "ncaambb:player:p1", "entity_id": "p1", "team_id": "150",
                "event_date": f"2025-12-{i:02d}", "stat_line": {"points": 20}, "started": True,
            }
            for i in range(1, 8)
        ]
        events = [_event(f"E{i}", f"2025-12-{i:02d}", "150", "153") for i in range(1, 8)]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=3)

        e7 = next(r for r in rows if r["event_key"] == "E7")
        assert e7["games_played"] == 3

    def test_skips_a_player_game_whose_event_is_missing_or_malformed(self):
        games = [
            {
                "event_key": "MISSING", "player_key": "ncaambb:player:p1", "entity_id": "p1", "team_id": "150",
                "event_date": "2025-12-01", "stat_line": {"points": 20}, "started": True,
            },
            {
                "event_key": "E2", "player_key": "ncaambb:player:p1", "entity_id": "p1", "team_id": "150",
                "event_date": "2025-12-08", "stat_line": {"points": 24}, "started": True,
            },
        ]
        events = [_event("E2", "2025-12-08", "150", "153")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        assert {row["event_key"] for row in rows} == {"E2"}

    def test_own_home_away_elo_and_rest_days_flow_through(self):
        games = [
            {
                "event_key": "E1", "player_key": "ncaambb:player:p1", "entity_id": "p1", "team_id": "153",
                "event_date": "2025-12-01", "stat_line": {"points": 20}, "started": True,
            },
        ]
        # p1's team (153) is the away side of E1.
        events = [_event("E1", "2025-12-01", "150", "153")]
        storage = self._storage(events, games)

        rows = build_dataset.build_player_dataset(storage, window=5)

        row = rows[0]
        assert row["is_home"] is False
        assert row["opponent_id"] == "150"
        assert row["own_elo"] is not None
        assert row["opponent_elo"] is not None
        assert row["rest_days"] is None  # no prior team-153 event in this fixture


def _poll(date_str, ranks):
    """ranks: {team_id: rank}, converted into the raw AP-poll shape
    ap_poll_to_rank_by_team expects (team.$ref carrying the trailing id)."""
    return {
        "date": f"{date_str}T07:00Z",
        "ranks": [
            {"current": rank, "team": {"$ref": f"http://sports.core.api.espn.com/.../teams/{team_id}?lang=en"}}
            for team_id, rank in ranks.items()
        ],
    }


class TestLoadRankings:
    def test_parses_season_type_week_from_the_key_and_date_from_the_poll(self):
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json"]
        raw_s3.get_json.return_value = _poll("2026-01-19", {"150": 1})

        polls = build_dataset._load_rankings(raw_s3)

        assert len(polls) == 1
        assert polls[0]["season"] == 2026
        assert polls[0]["season_type"] == 2
        assert polls[0]["week"] == 5
        assert polls[0]["as_of_date"] == "2026-01-19"
        assert polls[0]["rank_by_team"] == {"150": 1}

    def test_ignores_keys_that_dont_match_the_expected_shape(self):
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json", "ncaambb/teams.json"]
        raw_s3.get_json.return_value = _poll("2026-01-19", {"150": 1})

        polls = build_dataset._load_rankings(raw_s3)

        assert len(polls) == 1

    def test_sorted_oldest_first(self):
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/10.json", "ncaambb/rankings/2026/2/1.json"]
        raw_s3.get_json.side_effect = [_poll("2026-03-01", {}), _poll("2025-11-15", {})]

        polls = build_dataset._load_rankings(raw_s3)

        assert [p["as_of_date"] for p in polls] == ["2025-11-15", "2026-03-01"]

    def test_skips_a_poll_with_no_date(self):
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json"]
        raw_s3.get_json.return_value = {"ranks": []}  # no "date" key

        polls = build_dataset._load_rankings(raw_s3)

        assert polls == []


def _event_with_key(event_key, event_date, home_id, away_id, home_score=80, away_score=75):
    return {
        "event_key": event_key,
        "event_date": event_date,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


class TestTeamPreRating:
    def test_returns_home_pre_rating_for_the_home_team(self):
        event = _event_with_key("E1", "2026-01-01", "150", "153")
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1500.0}}

        assert build_dataset._team_pre_rating(event, "150", elo_ratings) == 1600.0

    def test_returns_away_pre_rating_for_the_away_team(self):
        event = _event_with_key("E1", "2026-01-01", "150", "153")
        elo_ratings = {"E1": {"home_pre_rating": 1600.0, "away_pre_rating": 1500.0}}

        assert build_dataset._team_pre_rating(event, "153", elo_ratings) == 1500.0


class TestResolveOwnElo:
    def test_prefers_the_most_recent_prior_event(self):
        prior = [
            _event_with_key("E1", "2026-01-01", "150", "999"),
            _event_with_key("E2", "2026-01-10", "150", "888"),
        ]
        elo_ratings = {
            "E1": {"home_pre_rating": 1500.0, "away_pre_rating": 1400.0},
            "E2": {"home_pre_rating": 1550.0, "away_pre_rating": 1400.0},
        }

        result = build_dataset._resolve_own_elo(prior, prior, "2026-01-19", "150", elo_ratings)

        assert result == 1550.0  # E2, the most recent prior event

    def test_falls_back_to_earliest_future_event_with_no_prior_history(self):
        season_events = [_event_with_key("E3", "2026-02-01", "150", "999")]
        elo_ratings = {"E3": {"home_pre_rating": 1620.0, "away_pre_rating": 1400.0}}

        result = build_dataset._resolve_own_elo([], season_events, "2026-01-19", "150", elo_ratings)

        assert result == 1620.0

    def test_none_with_no_season_events_at_all(self):
        result = build_dataset._resolve_own_elo([], [], "2026-01-19", "150", {})

        assert result is None


class TestBuildRankingDataset:
    def test_builds_one_row_per_ranked_team_per_poll(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json"]
        raw_s3.get_json.return_value = _poll("2026-01-19", {"150": 1, "153": 2})

        rows = build_dataset.build_ranking_dataset(storage, raw_s3)

        assert {row["team_id"] for row in rows} == {"150", "153"}
        assert all(row["as_of_date"] == "2026-01-19" for row in rows)

    def test_unranked_teams_never_appear(self):
        events = [_event_with_key("E1", "2026-01-01", "150", "160")]
        storage = MagicMock()
        storage.get_all_events.return_value = events
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json"]
        raw_s3.get_json.return_value = _poll("2026-01-19", {"150": 1})  # 160 never ranked

        rows = build_dataset.build_ranking_dataset(storage, raw_s3)

        assert {row["team_id"] for row in rows} == {"150"}

    def test_season_events_scoped_to_the_polls_own_season_only(self):
        events = [
            _event_with_key("E1", "2025-01-01", "150", "999"),  # different season
            _event_with_key("E2", "2026-01-10", "150", "888"),  # same season, prior to poll
        ]
        for event in events:
            event["season"] = 2025 if event["event_key"] == "E1" else 2026
        storage = MagicMock()
        storage.get_all_events.return_value = events
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json"]
        raw_s3.get_json.return_value = _poll("2026-01-19", {"150": 1})

        rows = build_dataset.build_ranking_dataset(storage, raw_s3)

        assert rows[0]["games_played"] == 1  # only E2 counts, not E1's different season

    def test_no_polls_yields_no_rows(self):
        storage = MagicMock()
        storage.get_all_events.return_value = []
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = []

        rows = build_dataset.build_ranking_dataset(storage, raw_s3)

        assert rows == []
