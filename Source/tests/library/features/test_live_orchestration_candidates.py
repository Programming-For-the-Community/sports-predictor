"""
Unit tests for library.features.live_orchestration.box_score_candidate_ids
-- the box-score leader-candidate search NBA/NCAAFB/NCAA MBB share. Each
sport's test_live_features.py covers it through that sport's own
_still_on_team check.
"""
from unittest.mock import MagicMock

from library.features.live_orchestration import box_score_candidate_ids


def _storage(events: list[dict], rows_by_event: dict[str, list[dict]], roster: list[dict] | None = None):
    storage = MagicMock()
    storage.get_team_events.return_value = events
    storage.get_player_game_stats_for_event.side_effect = lambda event_key: rows_by_event.get(event_key, [])
    storage.get_team_entities.return_value = roster or []
    return storage


def _player(entity_id):
    return {"entity_id": entity_id, "entity_type": "player"}


def _row(entity_id, team_id="13", **stats):
    return {"entity_id": entity_id, "team_id": team_id, "stat_line": stats}


def test_distinct_rostered_players_with_the_stat_most_recent_first():
    storage = _storage(
        [{"event_key": "E2", "season": 2026}, {"event_key": "E1", "season": 2026}],
        {
            "E2": [_row("b", points=10), _row("x", rebounds=4), _row("other", team_id="99", points=30)],
            "E1": [_row("a", points=12), _row("b", points=8), {"team_id": "13", "stat_line": {"points": 1}}],
        },
    )

    ids = box_score_candidate_ids(
        storage, "nba", "13", "2026-01-10", 2026, "points",
        season_lookback=1, still_on_team=lambda entity_id: entity_id != "a",
    )

    assert ids == ["b"]
    storage.get_team_events.assert_called_once_with("nba", "13", before_date="2026-01-10", events=None)


def test_stops_once_history_falls_outside_the_season_lookback():
    storage = _storage(
        [{"event_key": "E3", "season": 2026}, {"event_key": "E2", "season": 2025}, {"event_key": "E1", "season": 2024}],
        {"E3": [_row("c", points=1)], "E2": [_row("b", points=1)], "E1": [_row("a", points=1)]},
    )

    ids = box_score_candidate_ids(storage, "nba", "13", "2026-01-10", 2026, "points", season_lookback=1, still_on_team=lambda _: True)

    assert ids == ["c", "b"]


def test_no_candidates_skips_the_roster_check():
    still_on_team = MagicMock()

    assert box_score_candidate_ids(
        _storage([], {}), "nba", "13", "2026-01-10", None, "points", season_lookback=1, still_on_team=still_on_team,
    ) == []
    still_on_team.assert_not_called()


def test_rostered_players_without_a_box_score_for_the_team_follow_its_own_history():
    # A player who joined since the team's last game has no box score with it yet.
    storage = _storage(
        [{"event_key": "E1", "season": 2027}],
        {"E1": [_row("b", points=10)]},
        roster=[_player("b"), _player("new"), _player("waived"), {"entity_id": "13", "entity_type": "team"}],
    )

    ids = box_score_candidate_ids(
        storage, "nba", "13", "2026-10-21", 2027, "points",
        season_lookback=1, still_on_team=lambda entity_id: entity_id != "waived",
    )

    assert ids == ["b", "new"]
    storage.get_team_entities.assert_called_once_with("nba", "13")


def test_a_roster_with_no_team_history_still_yields_candidates():
    storage = _storage([], {}, roster=[_player("new")])

    ids = box_score_candidate_ids(
        storage, "nba", "13", "2026-10-21", 2027, "points", season_lookback=1, still_on_team=lambda _: True,
    )

    assert ids == ["new"]
