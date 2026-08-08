"""
Unit tests for the inference Lambda's live-feature-assembly module.
FeatureStorage is mocked -- these verify live_features.py's own
orchestration (which storage methods get called with what, how a
not-yet-played event's Elo/leader context gets built), not
build_event_features/build_player_features themselves, which already
have their own extensive coverage in tests/library/features/test_nfl.py.

storage.get_team_entities defaults to [] in every test that reaches
presumptive-leader selection but isn't specifically exercising the
roster-driven path -- MagicMock's default return value is a truthy,
non-iterable Mock, which _team_candidate_histories would crash trying to
iterate. Tests that ARE about roster-driven selection set it explicitly
to real roster rows instead.
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


def _roster(*entity_ids, as_of="2025-09-21"):
    # as_of matches every test's own event date by default -- "fresh" per
    # _is_roster_entry_fresh. Tests exercising staleness override it.
    return [{"entity_id": eid, "metadata": {"team_id_as_of": as_of}} for eid in entity_ids]


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
        storage.get_player_game_stats_for_event.return_value = []
        storage.get_player_game_stats.return_value = []

        row = live_features.build_live_event_features(storage, "nfl", "E2")

        assert row["event_key"] == "E2"
        # KC won E1 -- current_ratings (not pre_game_ratings, which has no
        # entry for E2 since it hasn't been processed) should reflect that.
        assert row["home_elo"] > 1500
        assert row["away_elo"] < 1500

    def test_presumptive_qb_comes_from_roster_average_when_no_depth_chart(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: _roster("mahomes-patrick") if team_id == "KC" else []

        def player_history(entity_id, before_date=None, limit=None):
            if entity_id == "mahomes-patrick":
                return [{"stat_line": {"passing_attempts": 30}, "event_date": "2025-09-14"}]
            return []
        storage.get_player_game_stats.side_effect = player_history

        live_features.build_live_event_features(storage, "nfl", "E3")

        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "mahomes-patrick" in called_entity_ids

    def test_retired_player_with_stale_roster_entry_falls_back_instead_of_surfacing(self):
        # Roster sync only ever upserts who IS on a fetched roster -- a
        # retired/cut player's entity row just sits frozen at whatever
        # team_id_as_of it last had, with nothing to ever clear it. Stale
        # by more than _ROSTER_STALENESS_DAYS must not be trusted as
        # "currently on this team", even though the box-score fallback
        # (older, no depth chart here either) has nothing for KC at all.
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            _roster("retired-qb", as_of="2020-01-01") if team_id == "KC" else []
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
        storage.get_team_entities.side_effect = lambda sport, team_id: _roster("mahomes-patrick") if team_id == "KC" else []
        storage.get_player_game_stats.return_value = [
            {"team_id": "DEN", "stat_line": {"passing_attempts": 35, "passing_yards": 260}, "event_date": "2025-09-14"},
        ]

        row = live_features.build_live_event_features(storage, "nfl", "E3")

        assert row["home_qb_avg_passing_yards"] == 260  # scored off the DEN-recorded history, not omitted
        called_entity_ids = {c.args[0] for c in storage.get_player_game_stats.call_args_list}
        assert "mahomes-patrick" in called_entity_ids

    def test_qb_selected_from_depth_chart_when_available_not_roster_average(self):
        storage = MagicMock()
        # Depth chart says "backup-qb" is QB1 -- roster-average selection
        # would pick "mahomes-patrick" instead (higher volume); depth
        # chart must win when both are available. Building the shared
        # roster-histories pool fetches both regardless (it's built for
        # the whole team once, ahead of any position-specific pick -- see
        # build_live_event_features' own comment on home_histories), so
        # the real assertion is on which player's stats the QB feature
        # ends up reflecting, not on who gets fetched at all.
        target = _event("E3", "2025-09-21", "KC", "LAC", home_depth_chart=_depth_chart(qb=["backup-qb"]))
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            _roster("mahomes-patrick", "backup-qb") if team_id == "KC" else []
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
        # Both listed QBs are hurt -- must NOT fall back to roster-average
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

    def test_falls_back_to_last_game_volume_when_no_depth_chart_or_roster(self):
        # No home_depth_chart on this event (older event, or a fetch
        # failure) AND roster sync hasn't reached this team either (no
        # team-index rows) -- must fall back to the original last-game
        # box-score behavior.
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")  # no depth chart
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_entities.return_value = []
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


class TestIsRosterEntryFresh:
    def test_same_day_is_fresh(self):
        entity = {"metadata": {"team_id_as_of": "2025-09-21"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is True

    def test_within_staleness_window_is_fresh(self):
        entity = {"metadata": {"team_id_as_of": "2025-09-10"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is True  # 11 days

    def test_beyond_staleness_window_is_not_fresh(self):
        entity = {"metadata": {"team_id_as_of": "2025-08-01"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is False  # 51 days

    def test_years_stale_is_not_fresh(self):
        # The exact reported bug: a retired player's roster entry frozen
        # years in the past.
        entity = {"metadata": {"team_id_as_of": "2020-01-01"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is False

    def test_missing_team_id_as_of_is_not_fresh(self):
        assert live_features._is_roster_entry_fresh({"metadata": {}}, "2025-09-21") is False

    def test_malformed_date_is_not_fresh(self):
        entity = {"metadata": {"team_id_as_of": "not-a-date"}}
        assert live_features._is_roster_entry_fresh(entity, "2025-09-21") is False


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

    def test_no_prior_game_or_roster_returns_empty_categories(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        storage.get_team_events.return_value = []  # neither team has a prior game
        storage.get_team_entities.return_value = []  # roster sync hasn't reached either team
        storage.get_all_events.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E1")

        assert candidates["home"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}
        assert candidates["away"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    def test_identifies_candidates_across_categories_from_current_roster(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            _roster("qb1", "wr1", "wr2", "rb1") if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "qb1": [{"stat_line": {"passing_attempts": 30}}],
                "wr1": [{"stat_line": {"receiving_targets": 10}}],
                "wr2": [{"stat_line": {"receiving_targets": 6}}],
                "rb1": [{"stat_line": {"rushing_attempts": 18}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        home = candidates["home"]
        assert len(home["passing"]) == 1 and home["passing"][0]["entity_id"] == "qb1"
        assert {row["entity_id"] for row in home["receiving"]} == {"wr1", "wr2"}
        assert {row["entity_id"] for row in home["rushing"]} == {"rb1"}
        # Away has no roster/prior game in this test -- confirms one
        # team's pool doesn't leak into the other's candidates.
        assert candidates["away"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    def test_traded_player_surfaces_under_new_team_via_roster(self):
        # wr1's only recorded game is for DEN, but the entities table
        # (roster sync) says they're on KC now -- must show up as a KC
        # receiving candidate, scored off their DEN-recorded history.
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: _roster("wr1") if team_id == "KC" else []
        storage.get_player_game_stats.return_value = [
            {"team_id": "DEN", "stat_line": {"receiving_targets": 9}, "event_date": "2025-09-14"},
        ]

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert "wr1" in {row["entity_id"] for row in candidates["home"]["receiving"]}

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
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        # Four distinct candidates (QB, 2 receivers, 1 rusher) is already
        # enough to prove the fix: pre-fix, each one would have separately
        # triggered its own get_all_events + Elo recompute.
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            _roster("qb1", "wr1", "wr2", "rb1") if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "qb1": [{"stat_line": {"passing_attempts": 30}}],
                "wr1": [{"stat_line": {"receiving_targets": 10}}],
                "wr2": [{"stat_line": {"receiving_targets": 6}}],
                "rb1": [{"stat_line": {"rushing_attempts": 18}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert storage.get_all_events.call_count == 1

    def test_sacks_ranked_by_own_average_not_last_game_volume(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            _roster("dl1", "dl2") if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "dl1": [{"stat_line": {"defensive_sacks": 3.0}}, {"stat_line": {"defensive_sacks": 0.0}}],
                "dl2": [{"stat_line": {"defensive_sacks": 1.0}}, {"stat_line": {"defensive_sacks": 1.0}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        # dl1 averages 1.5, dl2 averages 1.0 -- dl1 should lead despite
        # dl2's more consistent per-game total, confirming this is driven
        # by rank_by_average_stat's own average (already covered in
        # test_nfl.py), not a naive single-game-volume comparison.
        sack_ids = [row["entity_id"] for row in candidates["home"]["sacks"]]
        assert sack_ids == ["dl1", "dl2"]

    def test_receiving_candidates_from_depth_chart_skip_injured(self):
        storage = MagicMock()
        # Depth chart lists 3 WRs, wr2 is Out -- expect wr1 and wr3, NOT
        # the volume-based wr1/wr2 a roster-average comparison would pick.
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(wr=["wr1", "wr2", "wr3"]),
            home_injuries=[{"entity_id": "wr2", "status": "Out"}],
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            _roster("wr1", "wr2", "wr3") if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "wr1": [{"stat_line": {"receiving_targets": 10}}],
                "wr2": [{"stat_line": {"receiving_targets": 8}}],
                "wr3": [{"stat_line": {"receiving_targets": 2}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        receiving_ids = {row["entity_id"] for row in candidates["home"]["receiving"]}
        assert receiving_ids == {"wr1", "wr3"}

    def test_receiving_candidates_fall_back_to_last_game_volume_when_no_depth_chart_or_roster(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")  # no depth chart
        last_game = _event("E2", "2025-09-14", "KC", "DEN", 24, 17)
        storage.get_event.return_value = target
        storage.get_all_events.return_value = [last_game]
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_entities.return_value = []  # roster sync hasn't reached this team
        storage.get_team_events.side_effect = lambda sport, entity_id, before_date=None, limit=None: (
            [last_game] if entity_id == "KC" and limit == 1 else []
        )
        storage.get_player_game_stats_for_event.return_value = [
            {"entity_id": "wr1", "team_id": "KC", "stat_line": {"receiving_targets": 10}},
        ]
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert {row["entity_id"] for row in candidates["home"]["receiving"]} == {"wr1"}
