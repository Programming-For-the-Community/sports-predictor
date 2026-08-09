"""
Unit tests for live_features.build_live_event_leader_candidates -- the
round-robin candidate-pool identification (passing/receiving/rushing/
sacks) event_prediction.py's leaders block scores and ranks (see
test_predict_leaders.py/test_predict_receiving_props.py/
test_predict_rushing_props.py for the scoring/ranking/capping side of
that same feature). FeatureStorage is mocked. Split out of what used to
be one large test_live_features.py -- see test_live_features_event.py's
own history note.
"""
from datetime import date
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
    return {"entity_id": entity_id, "metadata": {"team_id_as_of": as_of or date.today().isoformat(), "position": position}}


class TestBuildLiveEventLeaderCandidates:
    def test_raises_when_event_not_found(self):
        storage = MagicMock()
        storage.get_event.return_value = None

        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_event_leader_candidates(storage, "nfl", "SPORT#NFL#EVENT#missing")

    def test_no_roster_returns_empty_categories(self):
        storage = MagicMock()
        storage.get_event.return_value = _event("E1", "2025-09-14", "KC", "LAC")
        storage.get_team_events.return_value = []
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
            [
                _roster_entry("qb1", "QB"), _roster_entry("wr1", "WR"),
                _roster_entry("wr2", "WR"), _roster_entry("rb1", "RB"),
            ] if team_id == "KC" else []
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
        # rb1 is also receiving-eligible (RB counts toward WR, see
        # _LEADER_POSITIONS) -- with exactly 3 receiving-eligible players
        # for 3 slots, all three show up, wr1/wr2 ranked ahead of rb1's
        # zero receiving_targets history.
        receiving_ids = [row["entity_id"] for row in home["receiving"]]
        assert receiving_ids[:2] == ["wr1", "wr2"]
        assert receiving_ids[2] == "rb1"
        # qb1 is also rushing-eligible (QB counts toward RB, for
        # scrambles) -- with only rb1/qb1 eligible for 2 slots, both show
        # up, rb1 ranked first on its actual rushing_attempts volume.
        rushing_ids = [row["entity_id"] for row in home["rushing"]]
        assert rushing_ids[0] == "rb1"
        assert set(rushing_ids) == {"rb1", "qb1"}
        # Away has no roster in this test -- confirms one team's pool
        # doesn't leak into the other's candidates.
        assert candidates["away"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}

    def test_rushing_pool_includes_a_receiving_rb_and_receiving_pool_includes_a_te(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("rb1", "RB"), _roster_entry("te1", "TE")] if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "rb1": [{"stat_line": {"rushing_attempts": 18, "receiving_targets": 4}}],
                "te1": [{"stat_line": {"receiving_targets": 7}}],
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert "rb1" in {row["entity_id"] for row in candidates["home"]["rushing"]}
        receiving_ids = {row["entity_id"] for row in candidates["home"]["receiving"]}
        assert receiving_ids == {"te1", "rb1"}  # both eligible for receiving

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
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("wr1", "WR")] if team_id == "KC" else []
        )
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
            [
                _roster_entry("qb1", "QB"), _roster_entry("wr1", "WR"),
                _roster_entry("wr2", "WR"), _roster_entry("rb1", "RB"),
            ] if team_id == "KC" else []
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

    def test_sacks_ranked_by_recent_window_volume_restricted_to_defense(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("dl1", "DE"), _roster_entry("dl2", "LB"), _roster_entry("qb1", "QB")] if team_id == "KC" else []
        )

        def player_history(entity_id, before_date=None, limit=None):
            histories = {
                "dl1": [{"stat_line": {"defensive_sacks": 3.0}}, {"stat_line": {"defensive_sacks": 2.0}}],  # total 5
                "dl2": [{"stat_line": {"defensive_sacks": 1.0}}, {"stat_line": {"defensive_sacks": 1.0}}],  # total 2
                "qb1": [],  # offense -- must never appear regardless of its own 0 sacks
            }
            return histories.get(entity_id, [])
        storage.get_player_game_stats.side_effect = player_history

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        sack_ids = [row["entity_id"] for row in candidates["home"]["sacks"]]
        assert sack_ids == ["dl1", "dl2"]
        assert "qb1" not in sack_ids

    def test_sacks_includes_a_zero_history_rookie_defender(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.side_effect = lambda sport, team_id: (
            [_roster_entry("rookie-lb", "LB")] if team_id == "KC" else []
        )
        storage.get_player_game_stats.return_value = []  # never played

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert "rookie-lb" in {row["entity_id"] for row in candidates["home"]["sacks"]}

    def test_receiving_candidates_from_depth_chart_skip_injured(self):
        storage = MagicMock()
        # Depth chart lists 3 WRs, wr2 is Out -- expect wr1 and wr3, NOT
        # the volume-based wr1/wr2 a recent-volume comparison would pick.
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
            [_roster_entry("wr1", "WR"), _roster_entry("wr2", "WR"), _roster_entry("wr3", "WR")] if team_id == "KC" else []
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

    def test_receiving_candidates_span_every_wr_slots_own_starter(self):
        # ESPN's real depth chart tracks three DISTINCT WR slots
        # (wr1/wr2/wr3), each its own full backup stack -- round-robin
        # order must put each slot's own starter ahead of any slot's
        # backup, not wr1's own top backups ahead of wr2/wr3's starters
        # (which is what taking n-deep from a single matched entry would
        # return).
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(wr_slots=[
                ["wr1-starter", "wr1-backup"], ["wr2-starter"], ["wr3-starter"],
            ]),
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        receiving = candidates["home"]["receiving"]
        assert [row["entity_id"] for row in receiving] == ["wr1-starter", "wr2-starter", "wr3-starter", "wr1-backup"]

    def test_receiving_candidates_are_not_capped_at_three(self):
        # Depth-chart RANK isn't a reliable predictor of actual production
        # across different slots (a WR2 backup can outproduce a WR3
        # starter) -- receiving deliberately takes every listed, healthy
        # player rather than guessing who to cut before they're even
        # scored, unlike QB/RB which stay capped.
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(wr_slots=[
                ["wr1-a", "wr1-b"], ["wr2-a", "wr2-b"], ["wr3-a", "wr3-b"],
            ]),
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert len(candidates["home"]["receiving"]) == 6

    def test_receiving_candidates_include_the_starting_te(self):
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(wr=["wr-starter"], te=["te-starter"]),
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        receiving_ids = {row["entity_id"] for row in candidates["home"]["receiving"]}
        assert receiving_ids == {"wr-starter", "te-starter"}

    def test_receiving_candidates_include_a_pass_catching_rb(self):
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(wr=["wr-starter"], rb=["rb-starter"]),
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        receiving_ids = {row["entity_id"] for row in candidates["home"]["receiving"]}
        assert receiving_ids == {"wr-starter", "rb-starter"}

    def test_rushing_candidates_include_a_scrambling_qb(self):
        storage = MagicMock()
        target = _event(
            "E3", "2025-09-21", "KC", "LAC",
            home_depth_chart=_depth_chart(qb=["qb-starter"], rb=["rb-starter", "rb-backup"]),
        )
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        rushing_ids = {row["entity_id"] for row in candidates["home"]["rushing"]}
        assert "qb-starter" in rushing_ids
        # Still capped at 2 (unlike receiving) -- QB competes for a slot
        # rather than being additive.
        assert len(candidates["home"]["rushing"]) == 2

    def test_no_roster_data_returns_empty_categories_not_a_fallback_pick(self):
        storage = MagicMock()
        target = _event("E3", "2025-09-21", "KC", "LAC")  # no depth chart
        storage.get_event.return_value = target
        storage.get_all_events.return_value = []
        storage.get_team_game_stats_for_team.return_value = []
        storage.get_team_events.return_value = []
        storage.get_team_entities.return_value = []  # roster sync hasn't reached this team
        storage.get_player_game_stats.return_value = []

        candidates = live_features.build_live_event_leader_candidates(storage, "nfl", "E3")

        assert candidates["home"] == {"passing": [], "receiving": [], "rushing": [], "sacks": []}
