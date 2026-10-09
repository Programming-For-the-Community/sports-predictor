"""
Edge cases for NFL's season_projection and live_features: malformed/
partial events are skipped, per-player feature failures drop only that
player, and the bracket section fails on its own.
"""
from unittest.mock import MagicMock, patch

import live_features
import season_projection
import season_simulation
from library.serving import model_loader

_NO_SIDES = {"event_key": "X", "event_id": "X", "season": 2025, "season_type": 3, "status": "completed", "participants": []}


def _game(event_key, home_id, away_id, home_score, away_score, status="completed", season_type=2):
    return {
        "event_key": event_key, "event_id": event_key, "event_date": "2025-10-05", "season": 2025,
        "season_type": season_type, "status": status,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score}},
        ],
    }


class TestMalformedEventsAreSkipped:
    def test_completed_records_skip_missing_sides_and_scores(self):
        wins, losses, *_ = season_projection._completed_game_records(
            [_NO_SIDES, _game("E2", "a", "b", None, 3), _game("E1", "a", "b", 24, 17)],
        )

        assert wins == {"a": 1, "b": 0}
        assert losses == {"a": 0, "b": 1}

    def test_remaining_games_skip_missing_sides(self):
        remaining, next_event = season_projection._remaining_game_inputs(
            [{**_NO_SIDES, "status": "scheduled"}, _game("S1", "a", "b", None, None, status="scheduled")],
        )

        assert remaining == [("a", "b")]
        assert next_event == {"a": "S1", "b": "S1"}

    def test_real_postseason_matchups_skip_missing_sides(self):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: (
            [_NO_SIDES, _game("P1", "a", "b", 30, 20, season_type=3)] if status == "completed" else []
        )

        assert list(season_projection._real_postseason_matchups(storage, 2025)) == [frozenset(("a", "b"))]


class TestFillRemainingFeatureRows:
    def test_keeps_only_players_whose_live_features_built(self):
        season_inputs = {"team_next_event": {"T1": "E-next"}, "current_ratings": {}, "team_last_completed_date": {}}
        player_team = {"ok": "T1", "missing-event": "T1", "boom": "T1", "no-team-game": "T2"}

        def _build(storage, sport, event_key, entity_id, current_ratings, team_last_event_dates, events):
            if entity_id == "missing-event":
                raise live_features.EventNotFoundError("gone")
            if entity_id == "boom":
                raise RuntimeError("bad row")
            return {"entity_id": entity_id}

        cache: dict = {}
        with patch.object(live_features, "build_live_player_features", side_effect=_build):
            season_projection._fill_remaining_feature_rows(MagicMock(), season_inputs, player_team, cache, set(player_team))

        assert cache == {"ok": {"entity_id": "ok"}}

    def test_reads_the_event_history_once_for_every_row(self):
        # Regression: each row used to re-read the full history itself, which
        # pushed the weekly run past Lambda's 10-minute limit.
        season_inputs = {"team_next_event": {"T1": "E-next"}, "current_ratings": {}, "team_last_completed_date": {}}
        player_team = {f"p{i}": "T1" for i in range(25)}
        storage = MagicMock()
        history = [{"event_key": "E-old"}]
        storage.get_all_events.return_value = history
        passed = []

        def _build(storage, sport, event_key, entity_id, current_ratings, team_last_event_dates, events):
            passed.append(events)
            return {"entity_id": entity_id}

        with patch.object(live_features, "build_live_player_features", side_effect=_build):
            season_projection._fill_remaining_feature_rows(storage, season_inputs, player_team, {}, set(player_team))

        storage.get_all_events.assert_called_once_with("nfl", status="completed")
        assert len(passed) == 25 and all(events is history for events in passed)


class TestProjectStatLeaderboard:
    def test_no_promoted_model_ranks_on_current_totals_alone(self):
        storage = MagicMock()
        storage.get_entity.return_value = {"name": "Player One"}
        stat = season_projection.PLAYER_PROP_STATS[0]

        with patch.object(season_projection.event_prediction, "get_cached_model", side_effect=model_loader.NoPromotedModelError("none")), \
             patch.object(model_loader, "predict") as predict:
            top = season_projection._project_stat_leaderboard(
                storage, MagicMock(), {}, {"games_remaining": {"T1": 5}}, stat, {"p1"},
                {stat: {"p1": 42.0}}, {"p1": {"f": 1}}, {"p1": "T1"},
            )

        predict.assert_not_called()
        assert top[0]["entity_id"] == "p1"
        assert top[0]["name"] == "Player One"


class TestBuildSeasonProjectionBracketFailure:
    def test_a_bracket_failure_falls_back_to_none(self):
        season_inputs = {
            "current_season": 2025, "wins": {}, "losses": {}, "ties": {}, "point_differential": {},
            "remaining_games": [], "current_ratings": {},
        }
        with patch.object(season_projection, "_season_standings_inputs", return_value=season_inputs), \
             patch.object(season_simulation, "simulate_season", return_value={}), \
             patch.object(season_projection, "enrich_team_standings", side_effect=lambda storage, sport, rows: rows), \
             patch.object(season_projection, "_leaderboards", return_value={}), \
             patch.object(season_projection, "_bracket_payload", side_effect=RuntimeError("boom")):
            result = season_projection.build_season_projection(MagicMock(), MagicMock(), MagicMock())

        assert result["bracket"] is None
        assert result["leaderboards"] == {}


class TestLiveFeatureHelpers:
    def test_no_leader_position_means_no_presumptive_history(self):
        storage = MagicMock()

        assert live_features._presumptive_leader_and_history(storage, "nfl", "T1", "2025-10-05", 5, position_abbreviation="K") == []
        storage.get_player_game_stats.assert_not_called()

    def test_known_last_event_dates_skip_the_team_history_lookup(self):
        storage = MagicMock()
        event = {"event_key": "E1", "event_date": "2025-10-12"}

        with patch.object(live_features, "build_player_features", return_value={"row": 1}) as build, \
             patch.object(live_features, "_live_elo_ratings", return_value={}), \
             patch.object(live_features, "live_matchup_columns", return_value={}):
            row = live_features._build_player_feature_row(
                storage, "nfl", event, "T1", "T2", "p1", "T1", [], 5, team_last_event_dates={"T1": "2025-10-05"},
            )

        assert row == {"row": 1}
        storage.get_team_events.assert_not_called()
        assert build.call_args.args[4] == "2025-10-05"
