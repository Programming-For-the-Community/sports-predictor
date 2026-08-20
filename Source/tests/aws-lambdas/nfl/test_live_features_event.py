"""
Unit tests for live_features.build_live_event_features -- the event-level
feature assembly (Elo, rolling stats, presumptive QB selection via depth
chart / roster-volume fallback) the inference Lambda's core win/margin/
score route uses. FeatureStorage is mocked.

storage.get_team_entities defaults to [] in every test that reaches
presumptive-leader selection but isn't specifically exercising the
roster-driven path -- MagicMock's default return value is a truthy,
non-iterable Mock, which _fresh_roster would crash trying to iterate.
Tests that ARE about roster-driven selection set it explicitly to real
roster rows instead.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import live_features


def _event(
    event_key, event_date, home_id, away_id, home_score=None, away_score=None,
    home_depth_chart=None, away_depth_chart=None, home_injuries=None, away_injuries=None,
):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    return {
        "event_key": event_key,
        "event_date": event_date,
        "week": 5,
        "season_type": 2,
        "venue_indoor": False,
        "venue_city": "Kansas City",
        "venue_state": "MO",
        "weather_temperature": 40,
        "home_depth_chart": home_depth_chart,
        "away_depth_chart": away_depth_chart,
        "home_injuries": home_injuries,
        "away_injuries": away_injuries,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }


def _depth_chart(qb=None, rb=None, wr=None, te=None, wr_slots=None):
    """Builds a filtered depth chart matching filter_depth_chart's output
    shape -- {position_code: {"position": {"abbreviation": ...},
    "athletes": [{"id": ...}, ...]}}. qb/rb/te are each a plain list of
    entity_id strings, already in rank order, for that position's one
    depth-chart slot. wr is shorthand for a single WR slot (most tests
    only need one); wr_slots is a list of per-slot lists for tests that
    need ESPN's real multiple-distinct-WR-slots shape (wr1/wr2/wr3, each
    its own backup stack -- see _depth_chart_entries' own docstring)."""
    chart = {}
    if qb is not None:
        chart["qb"] = {"position": {"abbreviation": "QB"}, "athletes": [{"id": eid} for eid in qb]}
    if rb is not None:
        chart["rb"] = {"position": {"abbreviation": "RB"}, "athletes": [{"id": eid} for eid in rb]}
    if wr is not None:
        chart["wr"] = {"position": {"abbreviation": "WR"}, "athletes": [{"id": eid} for eid in wr]}
    if te is not None:
        chart["te"] = {"position": {"abbreviation": "TE"}, "athletes": [{"id": eid} for eid in te]}
    for i, slot in enumerate(wr_slots or [], start=1):
        chart[f"wr{i}"] = {"position": {"abbreviation": "WR"}, "athletes": [{"id": eid} for eid in slot]}
    return chart


def _roster_entry(entity_id, position, as_of=None):
    # as_of defaults to real today -- "fresh" per _is_roster_entry_fresh,
    # which (via _fresh_roster's own default) judges freshness against
    # today, not the event's own date. Tests exercising staleness
    # override it with something clearly in the past.
    return {"entity_id": entity_id, "metadata": {"team_id_as_of": as_of or date.today().isoformat(), "position": position}}


class TestBuildLiveEventFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_features(storage, "nfl", "SPORT#NFL#EVENT#missing")

    def test_raises_when_event_missing_a_role(self):
        storage = MagicMock()
        storage.get_event.return_value = {
            "event_key": "E1", "event_date": "2025-09-14",
            "participants": [{"entity_id": "KC", "role": "home"}],
        }

        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_event_features(storage, "nfl", "E1")

    def test_assembles_event_level_features_for_an_unplayed_game(self):
        storage = MagicMock()
        target = _event("E2", "2025-09-14", "KC", "LAC")  # no scores -- not played yet
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [_event("E1", "2025-09-07", "KC", "LAC", 27, 20)]
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E2")

        assert row["event_key"] == "E2"
        # KC won E1 -- current_ratings (not pre_game_ratings, which has no
        # entry for E2 since it hasn't been processed) should reflect that.
        assert row["home_elo"] > 1500
        assert row["away_elo"] < 1500

    def test_presumptive_qb_comes_from_roster_when_no_depth_chart(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("mahomes-patrick", "QB")] if team_id == "KC" else []
        )
        storage.get_player_game_stats.return_value = [{"stat_line": {"passing_attempts": 30}, "event_date": "2025-09-14"}]

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "mahomes-patrick" in called_entity_ids

    def test_rookie_with_zero_history_is_still_eligible(self):
        # A currently-rostered player is a valid candidate regardless of
        # whether they've ever recorded a stat -- being on the roster is
        # what matters, not stat history (they could be a rookie).
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("rookie-qb", "QB")] if team_id == "KC" else []
        )
        storage.get_player_game_stats.return_value = []  # never played a game

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "rookie-qb" in called_entity_ids

    def test_roster_eligible_for_an_event_far_in_the_future(self):
        # Roster freshness is judged against today (when roster sync
        # actually wrote team_id_as_of), not the target event's own date
        # -- a season-simulation event two months out must not have every
        # roster entry rejected just for being far from a game that
        # hasn't been played yet. See _is_roster_entry_fresh's own
        # docstring.
        storage = MagicMock()
        far_future_date = (date.today() + timedelta(days=60)).isoformat()
        target = _event("E3", far_future_date, "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("future-qb", "QB")] if team_id == "KC" else []  # as_of defaults to today
        )
        storage.get_player_game_stats.return_value = []

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "future-qb" in called_entity_ids

    def test_ranks_by_recent_window_volume(self):
        # Both candidates' fetched history sums to a different total over
        # their own last window's worth of games -- veteran-qb's 3 games
        # (90 attempts) outranks hot-backup's 1 game (35 attempts).
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("veteran-qb", "QB"), _roster_entry("hot-backup", "QB")] if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "veteran-qb": [{"stat_line": {"passing_attempts": 30}}] * 3,  # total 90
                "hot-backup": [{"stat_line": {"passing_attempts": 35}}],  # total 35
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        live_features.build_live_event_features(storage, "nfl", "E3")

        called = storage.get_player_game_stats.call_args_list
        called_entity_ids = {c.args[0] for c in called}
        assert "veteran-qb" in called_entity_ids
        # Bounded to the rolling window, not a full unbounded history scan --
        # this is what keeps ranking cheap (one bounded query per candidate).
        assert all(c.kwargs["limit"] == live_features.DEFAULT_ROLLING_WINDOW for c in called)

    def test_retired_player_with_stale_roster_entry_is_not_eligible(self):
        # Roster sync only ever upserts who IS on a fetched roster -- a
        # retired/cut player's entity row just sits frozen at whatever
        # team_id_as_of it last had, with nothing to ever clear it. Stale
        # by more than _ROSTER_STALENESS_DAYS must not be trusted as
        # "currently on this team".
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("retired-qb", "QB", as_of="2020-01-01")] if team_id == "KC" else []
        )
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] is None
        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "retired-qb" not in called_entity_ids

    def test_traded_player_surfaces_under_new_team_using_past_performance(self):
        # mahomes-patrick's only game log is with DEN (his old team,
        # before the trade) -- the entities table (roster sync) already
        # says he's on KC now. He must show up as KC's presumptive QB,
        # scored off his DEN-recorded history, not be invisible until he
        # logs a game for KC.
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("mahomes-patrick", "QB")] if team_id == "KC" else []
        )
        storage.get_player_game_stats.return_value = [
            {"team_id": "DEN", "stat_line": {"passing_attempts": 35, "passing_yards": 260}, "event_date": "2025-09-14"},
        ]

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] == 260  # scored off the DEN-recorded history, not omitted
        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "mahomes-patrick" in called_entity_ids

    def test_departed_player_no_longer_surfaces_for_their_old_team(self):
        # Mirror of the trade test above: a player who's no longer on KC's
        # current roster must not show up as a KC candidate just because
        # their last recorded game was for KC -- there's no roster
        # membership left to make them eligible, and their old game log
        # (still sitting in player_game_stats) is never consulted for
        # eligibility at all.
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []  # not on KC's current roster anymore

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] is None
        storage.get_player_game_stats.assert_not_called()

    def test_qb_selected_from_depth_chart_when_available_not_roster_volume(self):
        storage = MagicMock()
        # Depth chart says "backup-qb" is QB1 -- recent-volume selection
        # would pick "mahomes-patrick" instead (higher volume); depth
        # chart must win when both are available.
        target = _event("E3", "2025-09-21", "KC", "LAC", home_depth_chart=_depth_chart(qb=["backup-qb"]))
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("mahomes-patrick", "QB"), _roster_entry("backup-qb", "QB")] if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "mahomes-patrick": [{"stat_line": {"passing_attempts": 30, "passing_yards": 300}}],
                "backup-qb": [{"stat_line": {"passing_attempts": 5, "passing_yards": 40}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] == 40  # backup-qb's, not mahomes-patrick's higher-volume 300

    def test_injured_qb_skipped_for_next_ranked_healthy_depth_chart_entry(self):
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(qb=["starter-qb", "backup-qb"]),
            home_injuries=[{"entity_id": "starter-qb", "status": "Out"}],
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "backup-qb" in called_entity_ids
        assert "starter-qb" not in called_entity_ids

    def test_no_healthy_depth_chart_entry_returns_empty_not_a_fallback_pick(self):
        # Both listed QBs are hurt -- must NOT fall through to roster-
        # volume selection, which could re-surface the very player just
        # excluded.
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(qb=["starter-qb", "backup-qb"]),
            home_injuries=[
                {"entity_id": "starter-qb", "status": "Out"},
                {"entity_id": "backup-qb", "status": "Doubtful"},
            ],
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] is None  # no candidate -> empty history, matches no-leader shape
        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "starter-qb" not in called_entity_ids

    def test_no_roster_data_returns_empty_not_a_wrong_pick(self):
        # No depth chart AND roster sync hasn't reached this team --
        # deliberately no fallback to last-game box score (that read a
        # player's team_id off their own most recent game log, exactly
        # the stale-team-association bug this design exists to avoid).
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")  # no depth chart
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] is None
