from unittest.mock import MagicMock

import pytest

from library.features import matchup


def _event(event_key, event_date, home, away):
    return {
        "event_key": event_key, "event_date": event_date,
        "participants": [{"entity_id": home, "role": "home"}, {"entity_id": away, "role": "away"}],
    }


def _row(event_key, team_id, entity_id, **stats):
    return {"event_key": event_key, "team_id": team_id, "entity_id": entity_id, "stat_line": stats}


EVENTS = [
    _event("E1", "2025-09-07", "DAL", "PHI"),
    _event("E2", "2025-09-14", "NYG", "PHI"),
    _event("E3", "2025-09-21", "KC", "PHI"),
]
ROWS = [
    _row("E1", "DAL", "d1", rushing_yards=80, rushing_attempts=20), _row("E1", "DAL", "d2", rushing_yards=40, rushing_attempts=5),
    _row("E1", "PHI", "p1", rushing_yards=10, defensive_sacks=2),
    _row("E2", "NYG", "n1", rushing_yards=60, defensive_sacks=1),
    _row("E2", "PHI", "p1", rushing_yards=0),
]
PLAYER_GAME = {"event_key": "E3", "event_date": "2025-09-21", "team_id": "KC", "entity_id": "k1"}


def _columns(lookup, prior_games=(), opponent="PHI"):
    return matchup.matchup_columns("nfl", PLAYER_GAME, opponent, list(prior_games), lookup, window=5)


class TestMatchupColumns:
    def test_opponent_allowed_averages_what_its_opponents_totaled(self):
        columns = _columns(matchup.InMemoryMatchupLookup(EVENTS, ROWS))

        assert columns["opp_allowed_rushing_yards"] == pytest.approx((120 + 60) / 2)
        assert columns["opp_allowed_defensive_sacks"] == pytest.approx(0.5)
        assert columns["opp_allowed_passing_yards"] == 0

    def test_usage_share_is_the_players_part_of_team_volume(self):
        prior = [_row("E1", "DAL", "d1", rushing_attempts=20)]

        columns = matchup.matchup_columns("nfl", PLAYER_GAME, "PHI", prior, matchup.InMemoryMatchupLookup(EVENTS, ROWS), 5)

        assert columns["share_rushing_attempts"] == pytest.approx(0.8)
        assert columns["share_receiving_targets"] is None

    def test_no_opponent_history_and_no_prior_games_leave_columns_empty(self):
        columns = _columns(matchup.InMemoryMatchupLookup(EVENTS, ROWS), opponent="SEA")

        assert columns["opp_allowed_rushing_yards"] is None
        assert columns["share_passing_attempts"] is None

    def test_an_unknown_opponent_leaves_allowed_columns_empty(self):
        assert _columns(matchup.InMemoryMatchupLookup(EVENTS, ROWS), opponent=None)["opp_allowed_rushing_yards"] is None

    def test_a_row_without_an_event_key_is_skipped(self):
        columns = _columns(matchup.InMemoryMatchupLookup(EVENTS, ROWS), prior_games=[{"team_id": "DAL", "stat_line": {"rushing_attempts": 3}}])

        assert columns["share_rushing_attempts"] is None

    def test_a_sport_without_matchup_stats_gets_nothing(self):
        assert matchup.matchup_columns("pga", PLAYER_GAME, "PHI", [], matchup.InMemoryMatchupLookup([], []), 5) == {}

    def test_only_games_before_the_event_count(self):
        late = _event("E9", "2025-09-28", "SEA", "PHI")
        lookup = matchup.InMemoryMatchupLookup([*EVENTS, late], [*ROWS, _row("E9", "SEA", "s1", rushing_yards=500)])

        assert _columns(lookup)["opp_allowed_rushing_yards"] == pytest.approx(90)


def _storage():
    storage = MagicMock()
    storage.get_team_events.side_effect = lambda sport, team_id, before_date, limit, events: [
        e for e in sorted(EVENTS, key=lambda e: e["event_date"], reverse=True)
        if e["event_date"] < before_date and team_id in {p["entity_id"] for p in e["participants"]}
    ][:limit]
    storage.get_player_game_stats_for_event.side_effect = lambda event_key: [r for r in ROWS if r["event_key"] == event_key]
    return storage


class TestStorageLookup:
    @pytest.fixture(autouse=True)
    def _empty_cache(self):
        matchup._event_rows_cache.clear()
        yield
        matchup._event_rows_cache.clear()

    def test_live_columns_match_training_columns_for_the_same_games(self):
        prior = [_row("E1", "DAL", "d1", rushing_attempts=20)]
        training = matchup.matchup_columns("nfl", PLAYER_GAME, "PHI", prior, matchup.InMemoryMatchupLookup(EVENTS, ROWS), 5)

        live = matchup.live_matchup_columns(_storage(), "nfl", PLAYER_GAME, "KC", "PHI", prior, 5)

        assert live == training

    def test_each_past_game_is_read_once_across_players(self):
        storage = _storage()

        for _ in range(3):
            matchup.live_matchup_columns(storage, "nfl", PLAYER_GAME, "KC", "PHI", [], 5)

        assert storage.get_player_game_stats_for_event.call_count == 2

    def test_a_full_cache_drops_its_oldest_game(self, monkeypatch):
        monkeypatch.setattr(matchup, "_EVENT_ROWS_MAX", 1)
        lookup = matchup.StorageMatchupLookup(_storage(), "nfl")

        lookup.team_totals("E1", "DAL")
        lookup.team_totals("E2", "NYG")

        assert list(matchup._event_rows_cache) == ["E2"]
