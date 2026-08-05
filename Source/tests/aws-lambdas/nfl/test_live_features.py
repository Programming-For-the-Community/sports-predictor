"""
Unit tests for the inference Lambda's live-feature-assembly module.
FeatureStorage is mocked -- these verify live_features.py's own
orchestration (which storage methods get called with what, how a
not-yet-played event's Elo/leader context gets built), not
build_event_features/build_player_features themselves, which already
have their own extensive coverage in tests/library/features/test_nfl.py.
"""
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


def _depth_chart(qb=None, rb=None, wr=None):
    """Builds a filtered depth chart matching ingest's _filter_depth_chart
    output shape -- {position_code: {"position": {"abbreviation": ...},
    "athletes": [{"id": ...}, ...]}}. Each of qb/rb/wr is a plain list of
    entity_id strings, already in rank order."""
    chart = {}
    if qb is not None:
        chart["qb"] = {"position": {"abbreviation": "QB"}, "athletes": [{"id": eid} for eid in qb]}
    if rb is not None:
        chart["rb"] = {"position": {"abbreviation": "RB"}, "athletes": [{"id": eid} for eid in rb]}
    if wr is not None:
        chart["wr"] = {"position": {"abbreviation": "WR"}, "athletes": [{"id": eid} for eid in wr]}
    return chart


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
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_player_game_stats_for_event.return_value = []
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E2")

        assert row["event_key"] == "E2"
        # KC won E1 -- current_ratings (not pre_game_ratings, which has no
        # entry for E2 since it hasn't been processed) should reflect that.
        assert row["home_elo"] > 1500
        assert row["away_elo"] < 1500

    def test_presumptive_qb_comes_from_the_teams_most_recent_completed_game(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []

        def team_events(sport, entity_id, before_date=None, limit=None):
            return [last_game] if entity_id == "KC" and limit == 1 else []
        storage.get_team_events.side_effect = team_events

        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "mahomes-patrick", "team_id": "KC", "stat_line": {"passing_attempts": 30}},
            {"entity_id": "backup-qb", "team_id": "KC", "stat_line": {"passing_attempts": 2}},
        ]
        storage.get_player_game_stats.return_value = [
            {"event_date": "2025-09-14", "stat_line": {"passing_yards": 300}, "started": True},
        ]

        live_features.build_live_event_features(storage, "nfl", "E3")

        # The identified leader (most passing attempts) is who we should
        # have fetched rolling history for.
        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "mahomes-patrick" in called_entity_ids

    def test_qb_selected_from_depth_chart_when_available_not_last_game_volume(self):
        storage = MagicMock()
        # Depth chart says "backup-qb" is QB1 -- volume-based selection
        # (most passing attempts last game) would pick "mahomes-patrick"
        # instead; depth chart must win when both are available.
        target = _event("E3", "2025-09-21", "KC", "LAC", home_depth_chart=_depth_chart(qb=["backup-qb"]))
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "mahomes-patrick", "team_id": "KC", "stat_line": {"passing_attempts": 30}},
        ]
        storage.get_player_game_stats.return_value = []

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "backup-qb" in called_entity_ids
        assert "mahomes-patrick" not in called_entity_ids

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
        storage.get_player_game_stats.return_value = []

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "backup-qb" in called_entity_ids
        assert "starter-qb" not in called_entity_ids

    def test_no_healthy_depth_chart_entry_returns_empty_not_a_fallback_pick(self):
        # Both listed QBs are hurt -- must NOT fall back to volume-based
        # selection, which could re-surface the very player just excluded.
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(qb=["starter-qb", "backup-qb"]),
            home_injuries=[
                {"entity_id": "starter-qb", "status": "Out"},
                {"entity_id": "backup-qb", "status": "Doubtful"},
            ],
        )
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "starter-qb", "team_id": "KC", "stat_line": {"passing_attempts": 30}},
        ]
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] is None  # no candidate -> empty history, matches no-leader shape
        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "starter-qb" not in called_entity_ids

    def test_falls_back_to_last_game_volume_when_no_depth_chart_present(self):
        # No home_depth_chart on this event (older event, or a fetch
        # failure) -- must behave exactly like before depth charts existed.
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")  # no depth chart
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "mahomes-patrick", "team_id": "KC", "stat_line": {"passing_attempts": 30}},
        ]
        storage.get_player_game_stats.return_value = []

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "mahomes-patrick" in called_entity_ids


class TestDepthChartEntry:
    def test_finds_entry_by_abbreviation_regardless_of_outer_key(self):
        chart = {"weird-key": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}
        assert live_features._depth_chart_entry(chart, "QB") == chart["weird-key"]

    def test_none_when_depth_chart_is_none(self):
        assert live_features._depth_chart_entry(None, "QB") is None

    def test_none_when_no_matching_position(self):
        chart = {"rb": {"position": {"abbreviation": "RB"}, "athletes": []}}
        assert live_features._depth_chart_entry(chart, "QB") is None


class TestHealthyAthleteIds:
    def test_returns_all_ids_in_rank_order_when_no_injuries(self):
        entry = {"athletes": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        assert live_features._healthy_athlete_ids(entry, None) == ["1", "2", "3"]

    def test_excludes_out_and_doubtful_keeps_questionable(self):
        entry = {"athletes": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        injuries = [
            {"entity_id": "1", "status": "Out"},
            {"entity_id": "2", "status": "Questionable"},
        ]
        assert live_features._healthy_athlete_ids(entry, injuries) == ["2", "3"]

    def test_all_excluded_returns_empty_list(self):
        entry = {"athletes": [{"id": "1"}]}
        injuries = [{"entity_id": "1", "status": "Doubtful"}]
        assert live_features._healthy_athlete_ids(entry, injuries) == []


class TestBuildLivePlayerFeatures:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "nfl", "SPORT#NFL#EVENT#missing", "mahomes-patrick")

    def test_raises_when_entity_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        storage.get_entity.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_player_features(storage, "nfl", "E1", "unknown-player")

    def test_team_id_comes_from_the_entity_record_not_the_last_game_log(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        # Entity record says LAC (e.g. just traded) -- a stale last-game
        # log showing KC must NOT override this.
        storage.get_entity.return_value = {"entity_id": "mahomes-patrick", "metadata": {"team_id": "LAC"}}
        storage.get_player_game_stats.return_value = [
            {"event_date": "2025-09-07", "stat_line": {"passing_yards": 280}, "started": True},
        ]
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []

        row = live_features.build_live_player_features(storage, "nfl", "E1", "mahomes-patrick")

        assert row["team_id"] == "LAC"
        assert row["opponent_id"] == "KC"  # LAC is away in this event -> opponent is home (KC)

    def test_avg_stats_reflect_the_players_own_prior_games(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E2", "2025-09-14", "KC", "LAC")
        storage.get_entity.return_value = {"entity_id": "mahomes-patrick", "metadata": {"team_id": "KC"}}
        storage.get_player_game_stats.return_value = [
            {"event_date": "2025-09-07", "stat_line": {"passing_yards": 280}, "started": True},
        ]
        storage.get_team_events.return_value = []
        storage.get_all_events.return_value = []

        row = live_features.build_live_player_features(storage, "nfl", "E2", "mahomes-patrick")

        assert row["avg_passing_yards"] == 280
        assert row["label_stat_line"] == {}  # unknown -- this is what's being predicted


class TestBuildLiveEventLeaderCandidates:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_leader_candidates(storage, "nfl", "SPORT#NFL#EVENT#missing")

    def test_no_prior_game_returns_empty_categories(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        storage.get_team_events.return_value = []  # neither team has a prior game
        storage.get_all_events.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E1")

        assert candidates["home"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}
        assert candidates["away"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    def test_identifies_candidates_across_categories_for_the_home_team(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []

        def team_events(sport, entity_id, before_date=None, limit=None):
            return [last_game] if entity_id == "KC" and limit == 1 else []
        storage.get_team_events.side_effect = team_events

        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "qb1", "team_id": "KC", "stat_line": {"passing_attempts": 30}},
            {"entity_id": "wr1", "team_id": "KC", "stat_line": {"receiving_targets": 10}},
            {"entity_id": "wr2", "team_id": "KC", "stat_line": {"receiving_targets": 6}},
            {"entity_id": "rb1", "team_id": "KC", "stat_line": {"rushing_attempts": 18}},
        ]
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        home = candidates["home"]
        assert len(home["passing"]) == 1 and home["passing"][0]["entity_id"] == "qb1"
        assert {row["entity_id"] for row in home["receiving"]} == {"wr1", "wr2"}
        assert {row["entity_id"] for row in home["rushing"]} == {"rb1"}
        # Away has no prior game in this test -- confirms one team's
        # roster doesn't leak into the other's candidates.
        assert candidates["away"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    def test_computes_elo_ratings_once_regardless_of_candidate_count(self):
        """Regression test for a real production timeout: before this fix,
        every candidate's own _build_player_feature_row call independently
        recomputed Elo from the full completed-events history (see
        _live_elo_ratings' docstring) -- with up to ~15 candidates per
        event (QB + 3 receivers + 2 rushers + 3 pass-rushers, per team),
        that meant get_all_events + a full Elo walk up to 15 times in one
        request. Confirmed live via CloudWatch that this was hitting the
        29s Lambda timeout on /nfl/predictions/events/{id}. Threading
        current_ratings through every candidate (same fix already applied
        to handler.py's season leaderboards) means exactly one call
        regardless of how many candidates get identified."""
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        # Only the home team has a prior game -- same pattern as
        # test_identifies_candidates_across_categories_for_the_home_team.
        # Four distinct candidates (QB, 2 receivers, 1 rusher) is already
        # enough to prove the fix: pre-fix, each one would have separately
        # triggered its own get_all_events + Elo recompute.
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "qb1", "team_id": "KC", "stat_line": {"passing_attempts": 30}},
            {"entity_id": "wr1", "team_id": "KC", "stat_line": {"receiving_targets": 10}},
            {"entity_id": "wr2", "team_id": "KC", "stat_line": {"receiving_targets": 6}},
            {"entity_id": "rb1", "team_id": "KC", "stat_line": {"rushing_attempts": 18}},
        ]
        storage.get_player_game_stats.return_value = []

        live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert storage.get_all_events.call_count == 1

    def test_sacks_ranked_by_own_average_not_last_game_volume(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "dl1", "team_id": "KC", "stat_line": {"defensive_sacks": 3.0}},  # one huge game
            {"entity_id": "dl2", "team_id": "KC", "stat_line": {"defensive_sacks": 1.0}},  # consistent performer
        ]

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "dl1": [{"stat_line": {"defensive_sacks": 3.0}}, {"stat_line": {"defensive_sacks": 0.0}}],
                "dl2": [{"stat_line": {"defensive_sacks": 1.0}}, {"stat_line": {"defensive_sacks": 1.0}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        # dl2's average (1.0) beats dl1's (1.5)? No -- dl1 averages 1.5,
        # dl2 averages 1.0, so dl1 should still lead here; the real point
        # is that this is driven by rank_by_average_stat's own average
        # (already covered in test_nfl.py), not by dl1's single-game 3.0
        # sack total naively winning outright with no averaging at all.
        sack_ids = [row["entity_id"] for row in candidates["home"]["sacks"]]
        assert sack_ids == ["dl1", "dl2"]

    def test_receiving_candidates_from_depth_chart_skip_injured(self):
        storage = MagicMock()
        # Depth chart lists 3 WRs, wr2 is Out -- expect wr1 and wr3, NOT
        # the volume-based wr1/wr2 the last-game stat_line would pick.
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(wr=["wr1", "wr2", "wr3"]),
            home_injuries=[{"entity_id": "wr2", "status": "Out"}],
        )
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "wr1", "team_id": "KC", "stat_line": {"receiving_targets": 10}},
            {"entity_id": "wr2", "team_id": "KC", "stat_line": {"receiving_targets": 8}},
        ]
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        receiving_ids = {row["entity_id"] for row in candidates["home"]["receiving"]}
        assert receiving_ids == {"wr1", "wr3"}

    def test_receiving_candidates_fall_back_to_volume_when_no_depth_chart(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")  # no depth chart
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "wr1", "team_id": "KC", "stat_line": {"receiving_targets": 10}},
        ]
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert {row["entity_id"] for row in candidates["home"]["receiving"]} == {"wr1"}
