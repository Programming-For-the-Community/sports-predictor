"""
Unit tests for the NHL season projection's wiring: records from completed
games, the standings row shape, the leaderboard inputs and the scheduled
write. Storage, models and live feature building are mocked.

conftest.py puts aws-lambdas/nhl/predict on sys.path.
"""
from unittest.mock import MagicMock, patch

import live_features
import season_projection
import season_simulation

from library.serving import season_projection_common

SEASON = 2027


def _event(event_id, home, away, home_score=None, away_score=None, *, status="completed", season=SEASON, season_type=2,
           date="2026-10-10", overtime=False):
    def participant(entity_id, role, score):
        return {"entity_id": entity_id, "role": role, "result": {} if score is None else {"score": score}}

    return {
        "event_key": f"SPORT#NHL#EVENT#{event_id}", "event_id": str(event_id), "event_date": date, "status": status,
        "season": season, "season_type": season_type, "went_to_overtime": overtime,
        "participants": [participant(home, "home", home_score), participant(away, "away", away_score)],
    }


def _storage(completed, scheduled, player_stats=()):
    storage = MagicMock()
    storage.get_all_events.side_effect = lambda sport, status="completed": {"completed": completed, "scheduled": scheduled}[status]
    storage.get_all_player_game_stats.return_value = list(player_stats)
    storage.get_entity.side_effect = lambda sport, entity_id, kind: {"name": f"Name {entity_id}", "metadata": {"abbreviation": f"A{entity_id}"}}
    return storage


COMPLETED = [
    _event(1, "1", "2", 4, 2, date="2026-10-08"),
    _event(2, "2", "1", 3, 2, date="2026-10-09", overtime=True),
    _event(3, "1", "5", 1, 5, date="2026-10-10"),
    _event(4, "1", "2", 6, 0, date="2026-09-25", season_type=1),
    _event(5, "1", "2", 3, 1, date="2026-04-01", season=2026),
    _event(6, "90001", "90002", 9, 8, date="2026-10-07"),
]
SCHEDULED = [
    _event(10, "5", "1", status="scheduled", date="2026-10-12"),
    _event(11, "2", "5", status="scheduled", date="2026-10-13"),
    _event(12, "1", "2", status="scheduled", date="2027-04-20", season_type=3),
]


class TestSeasonInputs:
    def _inputs(self):
        return season_projection._season_inputs(_storage(COMPLETED, SCHEDULED))

    def test_records_count_this_seasons_regular_season_franchise_games_only(self):
        inputs = self._inputs()

        assert inputs["current_season"] == SEASON
        assert inputs["wins"] == {"1": 1, "2": 1, "5": 1}
        # An overtime loss is its own column and earns no regulation win.
        assert inputs["losses"] == {"2": 1, "1": 1}
        assert inputs["overtime_losses"] == {"1": 1}
        assert inputs["regulation_wins"] == {"1": 1, "5": 1}
        assert inputs["completed_event_keys"] == {f"SPORT#NHL#EVENT#{n}" for n in (1, 2, 3)}
        assert inputs["season_start_date"] == "2026-10-08"

    def test_remaining_schedule_is_regular_season_and_next_event_is_the_soonest(self):
        inputs = self._inputs()

        assert inputs["remaining_games"] == [("5", "1"), ("2", "5")]
        assert dict(inputs["games_remaining"]) == {"5": 2, "1": 1, "2": 1}
        assert inputs["team_next_event"] == {
            "5": "SPORT#NHL#EVENT#10", "1": "SPORT#NHL#EVENT#10", "2": "SPORT#NHL#EVENT#11",
        }

    def test_history_excludes_non_franchise_games(self):
        assert "SPORT#NHL#EVENT#6" not in {e["event_key"] for e in self._inputs()["history"]}


class TestBuildSeasonProjection:
    def _build(self, storage=None, leaderboards=None):
        storage = storage or _storage(COMPLETED, SCHEDULED)
        real_simulate = season_simulation.simulate_season
        with patch.object(season_projection, "_leaderboards", **(leaderboards or {"return_value": {"goals": []}})), \
             patch.object(
                 season_simulation, "simulate_season",
                 side_effect=lambda *args, **kwargs: real_simulate(*args, **kwargs, simulations=20, seed=3),
             ):
            return season_projection.build_season_projection(storage, MagicMock())

    def test_payload_has_the_shared_season_shape(self):
        result = self._build()

        assert set(result) == {"sport", "season", "standings", "leaderboards", "bracket", "generated_at"}
        assert (result["sport"], result["season"]) == ("nhl", SEASON)
        assert len(result["standings"]) == 32

    def test_standings_rows_carry_overtime_losses_as_ties(self):
        row = next(row for row in self._build()["standings"] if row["team_id"] == "1")

        assert (row["wins"], row["losses"], row["ties"]) == (1, 1, 1)
        assert row["division"] == "Eastern Atlantic"
        assert row["abbreviation"] == "A1"
        for field in ("projected_wins", "projected_losses", "division_winner_probability", "playoff_probability", "championship_probability"):
            assert isinstance(row[field], float)

    def test_bracket_names_every_team_in_it(self):
        bracket = self._build()["bracket"]

        assert set(bracket["conferences"]) == {"Eastern", "Western"}
        assert bracket["finals"]["team_a"] in bracket["team_names"]
        assert bracket["champion"] in bracket["team_names"]

    def test_a_leaderboard_failure_still_returns_standings_and_bracket(self):
        result = self._build(leaderboards={"side_effect": RuntimeError("boom")})

        assert result["leaderboards"] is None
        assert result["standings"]
        assert result["bracket"]


class TestLeaderboards:
    def test_totals_come_from_this_seasons_games_and_goalies_feed_saves_only(self):
        stats = [
            {"entity_id": "s1", "team_id": "1", "event_key": "SPORT#NHL#EVENT#1", "stat_line": {"goals": 2, "assists": 1, "shots_total": 5, "hits": 3}},
            {"entity_id": "s1", "team_id": "1", "event_key": "SPORT#NHL#EVENT#4", "stat_line": {"goals": 9}},
            {"entity_id": "g1", "team_id": "1", "event_key": "SPORT#NHL#EVENT#1", "stat_line": {"saves": 30}},
        ]
        storage = _storage(COMPLETED, SCHEDULED, stats)
        inputs = season_projection._season_inputs(storage)
        candidates = {
            "home": {"skaters": [{"entity_id": "s2", "team_id": "5"}], "goalie": {"entity_id": "g5", "team_id": "5"}},
            "away": {"skaters": [{"entity_id": "s1", "team_id": "1"}], "goalie": None},
        }
        captured = {}

        def leaderboard(storage, sport, s3, model_cache, season_inputs, stat, stat_candidates, totals, rows, player_team, **kwargs):
            captured[stat] = (set(stat_candidates), dict(totals[stat]), set(rows))
            return []

        with patch.object(live_features, "build_live_event_leader_candidates", return_value=candidates) as build, \
             patch.object(live_features, "build_live_player_features", side_effect=lambda *a, **k: {"entity_id": a[3]}) as build_player, \
             patch.object(season_projection_common, "project_stat_leaderboard", side_effect=leaderboard):
            result = season_projection._leaderboards(storage, MagicMock(), inputs)

        assert set(result) == {"goals", "assists", "shots_total", "hits", "saves"}
        storage.get_all_player_game_stats.assert_called_once_with("nhl", since_date="2026-10-08")
        # The preseason line is left out of the totals.
        assert captured["goals"][1] == {"s1": 2}
        assert captured["goals"][0] == {"s1", "s2"}
        assert captured["saves"][0] == {"g1", "g5"}
        # One build per next event, sharing the history read once.
        assert build.call_count == 2
        assert all(call.kwargs["events"] is inputs["history"] for call in build.call_args_list)
        # g1 leads saves without being a next-game starter, so gets his own row.
        assert [call.args[3] for call in build_player.call_args_list] == ["g1"]
        assert captured["saves"][2] == {"s1", "s2", "g5", "g1"}


class TestRunScheduled:
    def test_writes_the_projection_to_the_season_cache_key(self):
        bucket = MagicMock()
        with patch.object(season_projection, "build_season_projection", return_value={"sport": "nhl"}) as build:
            result = season_projection.run_scheduled("storage", bucket)

        assert result == {"status": "ok"}
        build.assert_called_once_with("storage", bucket)
        bucket.put_json.assert_called_once_with("season-projections/nhl/latest.json", {"sport": "nhl"})


class TestSkippedEvents:
    def test_a_game_without_both_scores_or_both_sides_is_not_counted(self):
        completed = [
            _event(1, "1", "2", 4, None),
            _event(2, "1", "2", 3, 3),
            {**_event(3, "1", "2", 2, 1), "participants": [{"entity_id": "1", "role": "home", "result": {"score": 2}}]},
        ]
        scheduled = [{**_event(10, "1", "2", status="scheduled"), "participants": []}]
        with patch.object(season_projection.nhl_teams, "is_real_franchise_matchup", return_value=True):
            inputs = season_projection._season_inputs(_storage(completed, scheduled))

        assert inputs["wins"] == {}
        assert inputs["losses"] == {}
        assert inputs["remaining_games"] == []


class TestCandidateRows:
    def test_an_event_whose_candidates_fail_is_left_out(self):
        inputs = {"team_next_event": {"1": "A", "2": "A", "5": "B"}}

        def build(storage, sport, event_key, events=None):
            if event_key == "B":
                raise RuntimeError("boom")
            return {"home": {"skaters": [{"entity_id": "s1"}], "goalie": {"entity_id": "g1"}}, "away": {"skaters": [], "goalie": None}}

        with patch.object(live_features, "build_live_event_leader_candidates", side_effect=build):
            rows = season_projection._candidate_rows(MagicMock(), inputs, [])

        assert rows["goaltending"] == [{"entity_id": "g1"}]
        assert rows["scoring"] == rows["shooting"] == rows["physical"] == [{"entity_id": "s1"}]


class TestBracketFailure:
    def test_a_bracket_failure_still_returns_standings(self):
        with patch.object(season_projection, "_leaderboards", return_value={}), \
             patch.object(season_projection, "_bracket", side_effect=RuntimeError("boom")):
            result =season_projection.build_season_projection(_storage(COMPLETED, SCHEDULED), MagicMock())

        assert result["bracket"] is None
        assert len(result["standings"]) == 32
