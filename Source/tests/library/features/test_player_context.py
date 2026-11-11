"""Unit tests for library.features.player_context and the MatchupLookup player history it reads."""
import math
from unittest.mock import MagicMock

from library.features import matchup, player_context


def _event(key, date, home="BOS", away="NY", **extra):
    if "home_injuries" in extra:
        extra.setdefault("injuries_as_of", "2025-11-05T23:00:00+00:00")
    return {
        "event_key": key, "event_date": date,
        "participants": [{"entity_id": home, "role": "home"}, {"entity_id": away, "role": "away"}],
        **extra,
    }


def _player(entity_id, key, date, team, **stats):
    return {"entity_id": entity_id, "event_key": key, "event_date": date, "team_id": team, "stat_line": stats, "started": True}


PAST = [_event("E1", "2025-11-01"), _event("E2", "2025-11-03")]
ROWS = [
    _player("star", "E1", "2025-11-01", "BOS", minutes=36, field_goal_attempts=20, points=30, field_goals_made=12,
            three_pointers_made=3, offensive_rebounds=2, turnovers=3, free_throw_attempts=6),
    _player("bench", "E1", "2025-11-01", "BOS", minutes=12, field_goal_attempts=5, points=4, field_goals_made=2,
            three_pointers_made=0, offensive_rebounds=1, turnovers=1, free_throw_attempts=0),
    _player("rival", "E1", "2025-11-01", "NY", minutes=40, field_goal_attempts=25, points=28, field_goals_made=11,
            three_pointers_made=2, offensive_rebounds=3, turnovers=2, free_throw_attempts=8),
    _player("star", "E2", "2025-11-03", "BOS", minutes=30, field_goal_attempts=15, points=20, field_goals_made=8,
            three_pointers_made=2, offensive_rebounds=1, turnovers=2, free_throw_attempts=4),
    _player("bench", "E2", "2025-11-03", "BOS", minutes=18, field_goal_attempts=5, points=6, field_goals_made=3,
            three_pointers_made=0, offensive_rebounds=0, turnovers=0, free_throw_attempts=0),
    _player("rival", "E2", "2025-11-03", "NY", minutes=38, field_goal_attempts=22, points=25, field_goals_made=10,
            three_pointers_made=1, offensive_rebounds=2, turnovers=3, free_throw_attempts=6),
]
LOOKUP = matchup.InMemoryMatchupLookup(PAST, ROWS)
GAME = {"entity_id": "bench", "event_key": "E3", "event_date": "2025-11-05", "team_id": "BOS"}


class TestPlayerGamesBefore:
    def test_in_memory_most_recent_first_and_capped(self):
        games = LOOKUP.player_games_before("star", "2025-11-05", 1)
        assert [g["event_key"] for g in games] == ["E2"]
        assert LOOKUP.player_games_before("star", "2025-11-03", 10)[0]["event_key"] == "E1"

    def test_storage_lookup_reads_once_per_ttl(self):
        storage = MagicMock()
        storage.get_player_game_stats.return_value = ["row"]
        first = matchup.StorageMatchupLookup(storage, "nba")
        second = matchup.StorageMatchupLookup(storage, "nba")
        assert first.player_games_before("p-cache", "2025-11-05", 10) == ["row"]
        assert second.player_games_before("p-cache", "2025-11-05", 10) == ["row"]
        storage.get_player_game_stats.assert_called_once_with("p-cache", before_date="2025-11-05", limit=10, sport="nba")


class TestInjuryColumns:
    def test_teammates_out_shares_and_own_status(self):
        event = _event("E3", "2025-11-05", home_injuries=[
            {"entity_id": "star", "status": "Out"}, {"entity_id": "bench", "status": "Questionable"},
        ])
        columns = player_context.injury_columns("nba", GAME, event, LOOKUP)
        assert columns["own_injury_status"] == 1
        assert math.isclose(columns["teammates_out_minutes_share"], (36 / 48 + 30 / 48) / 2)
        assert math.isclose(columns["teammates_out_fga_share"], (20 / 25 + 15 / 20) / 2)

    def test_nobody_out_is_zero_and_no_report_is_none(self):
        healthy = player_context.injury_columns("nba", GAME, _event("E3", "2025-11-05", home_injuries=[]), LOOKUP)
        assert (healthy["own_injury_status"], healthy["teammates_out_minutes_share"]) == (0, 0)
        unknown = player_context.injury_columns("nba", GAME, _event("E3", "2025-11-05"), LOOKUP)
        assert unknown == {"own_injury_status": None, "teammates_out_minutes_share": None, "teammates_out_fga_share": None}

    def test_nfl_team_qb_status_comes_from_an_injured_passer(self):
        events = [_event("F1", "2025-10-01", home="KC", away="LV")]
        rows = [
            _player("qb", "F1", "2025-10-01", "KC", passing_attempts=35),
            _player("rb", "F1", "2025-10-01", "KC", passing_attempts=1, rushing_attempts=20),
        ]
        lookup = matchup.InMemoryMatchupLookup(events, rows)
        game = {"entity_id": "wr", "event_key": "F2", "event_date": "2025-10-08", "team_id": "KC"}
        event = _event("F2", "2025-10-08", home="KC", away="LV", home_injuries=[
            {"entity_id": "qb", "status": "Doubtful"}, {"entity_id": "rb", "status": "Out"},
        ])
        columns = player_context.injury_columns("nfl", game, event, lookup)
        assert columns["team_qb_injury_status"] == 2
        assert columns["teammates_out_carry_share"] == 1.0

    def test_a_report_without_a_pregame_stamp_is_ignored(self):
        # Attached after the game, before reports were stamped.
        event = _event("E3", "2025-11-05", home_injuries=[{"entity_id": "star", "status": "Out"}], injuries_as_of=None)
        columns = player_context.injury_columns("nba", GAME, event, LOOKUP)
        assert columns["own_injury_status"] is None and columns["teammates_out_minutes_share"] is None

    def test_other_sports_get_own_status_only(self):
        event = _event("E3", "2025-11-05", home_injuries=[{"entity_id": "bench", "status": "Out"}])
        assert player_context.injury_columns("nhl", GAME, event, LOOKUP) == {"own_injury_status": 3}


class TestTeamColumns:
    def test_basketball_pace_and_opponent_defence(self):
        columns = player_context.basketball_columns(GAME, "NY", LOOKUP)
        bos = [25 - 3 + 4 + 0.44 * 6, 20 - 1 + 2 + 0.44 * 4]
        ny = [25 - 3 + 2 + 0.44 * 8, 22 - 2 + 3 + 0.44 * 6]
        paces = [(b + n) / 2 for b, n in zip(bos, ny)]
        assert math.isclose(columns["projected_pace"], sum(paces) / 2)
        # Boston was New York's opponent both times.
        assert math.isclose(columns["opp_def_rating"], 100 * (34 + 26) / sum(paces))
        assert math.isclose(columns["opp_efg_allowed"], (14 + 1.5 + 11 + 1) / 45)

    def test_football_opponent_pass_rush_and_sack_rates(self):
        events = [_event("F1", "2025-10-01", home="KC", away="LV")]
        rows = [
            _player("qb", "F1", "2025-10-01", "KC", passing_yards=250, passing_attempts=34, rushing_yards=90, rushing_attempts=25),
            _player("edge", "F1", "2025-10-01", "LV", defensive_sacks=2),
        ]
        game = {"entity_id": "wr", "event_key": "F2", "event_date": "2025-10-08", "team_id": "DEN"}
        columns = player_context.football_columns(game, "LV", matchup.InMemoryMatchupLookup(events, rows))
        assert columns == {"opp_yards_per_pass_allowed": 250 / 34, "opp_yards_per_rush_allowed": 90 / 25, "opp_sack_rate": 2 / 36}

    def test_unknown_opponent(self):
        columns = player_context.basketball_columns(GAME, None, LOOKUP)
        assert columns["projected_pace"] is None and columns["opp_def_rating"] is None


class TestTrendAndContext:
    def test_minutes_trend_and_starts(self):
        columns = player_context.trend_columns("nba", GAME, LOOKUP)
        assert columns == {"starts_rate_l10": 1.0, "minutes_trend": 0.0}

    def test_context_columns_through_matchup_columns(self):
        event = _event("E3", "2025-11-05", home_injuries=[])
        columns = matchup.matchup_columns("nba", GAME, "NY", [], LOOKUP, 5, event=event)
        assert {"own_injury_status", "projected_pace", "minutes_trend", "opp_allowed_points"} <= set(columns)

    def test_matchup_columns_without_an_event_skip_the_context(self):
        assert "own_injury_status" not in matchup.matchup_columns("nba", GAME, "NY", [], LOOKUP, 5)
