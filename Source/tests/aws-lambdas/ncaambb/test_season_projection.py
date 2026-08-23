"""
Unit tests for the NCAA MBB inference Lambda's scheduled season-
projection path: season_projection._season_standings_inputs (wins/
losses/conference-record/Elo derivation from stored events, with
team_conference read from schedule-sync's own daily S3 cache rather than
resolved live here -- this Lambda has no route to the public internet at
all, see season_projection.py's own docstring), the game-classification
helpers (_is_regular_season_game/_is_conference_tournament_game/
_is_march_madness_game), _resolve_matchup's 3-state reconciliation, and
the EventBridge-triggered ScheduledSeasonProjection handler branch.

The ncaambb_predict module is registered in sys.modules by conftest.py,
whose reset_ncaambb_predict_singletons fixture (autouse) resets
ncaambb_predict._storage/_model_bucket/_predictions_table/_raw_bucket
before and after every test here.
"""
from unittest.mock import MagicMock, patch

import model_loader
import ncaambb_predict
import season_projection
import season_simulation


def _completed_event(event_key, season, home_id, away_id, home_score, away_score, *,
                      event_id=None, event_date="2026-01-14", conference_competition=True, tournament_note=None):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": 2, "status": "completed",
        "conference_competition": conference_competition,
        **({"tournament_note": tournament_note} if tournament_note else {}),
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"score": home_score, "won": home_score > away_score}},
            {"entity_id": away_id, "role": "away", "result": {"score": away_score, "won": away_score > home_score}},
        ],
    }


def _scheduled_event(event_key, season, event_date, home_id, away_id, *,
                      event_id=None, season_type=2, conference_competition=True, tournament_note=None):
    return {
        "event_key": event_key, "event_id": event_id or event_key, "event_date": event_date,
        "season": season, "season_type": season_type, "status": "scheduled",
        "conference_competition": conference_competition,
        **({"tournament_note": tournament_note} if tournament_note else {}),
        "participants": [
            {"entity_id": home_id, "role": "home", "result": None},
            {"entity_id": away_id, "role": "away", "result": None},
        ],
    }


def _model_card(version: int) -> dict:
    return {"version": version, "algorithm": "fake", "feature_columns": []}


def _raw_bucket(team_conference: dict[str, str] | None, season: int = 2026):
    """A stand-in for the S3Manager instance season_projection.py reads
    schedule-sync's own conference-membership cache through. None means
    "cache object doesn't exist yet" (a brand-new season, or a transient
    write failure)."""
    bucket = MagicMock()
    if team_conference is None:
        bucket.object_exists.return_value = False
    else:
        bucket.object_exists.return_value = True
        bucket.get_json.return_value = {"season": season, "team_conference": team_conference}
    return bucket


class TestGameClassification:
    def test_regular_season_game_has_no_tournament_note(self):
        event = _completed_event("E1", 2026, "12", "24", 70, 60)
        assert season_projection._is_regular_season_game(event) is True
        assert season_projection._is_conference_tournament_game(event) is False
        assert season_projection._is_march_madness_game(event) is False

    def test_conference_tournament_game_is_season_type_2_with_a_note(self):
        event = _completed_event("E1", 2026, "12", "24", 70, 60, tournament_note="ACC Tournament")
        assert season_projection._is_regular_season_game(event) is False
        assert season_projection._is_conference_tournament_game(event) is True
        assert season_projection._is_march_madness_game(event) is False

    def test_march_madness_game_matches_the_real_headline_prefix(self):
        event = _completed_event("E1", 2026, "12", "24", 70, 60, conference_competition=False)
        event["season_type"] = 3
        event["tournament_note"] = "Men's Basketball Championship - South Region - First Four"
        assert season_projection._is_march_madness_game(event) is True

    def test_nit_shares_the_signature_but_is_not_march_madness(self):
        # Same type/flag signature as an NCAA-tournament game -- only the
        # notes headline text tells them apart. Confirmed live, see
        # project-ncaambb-onboarding memory.
        event = _completed_event("E1", 2026, "12", "24", 70, 60, conference_competition=False)
        event["season_type"] = 3
        event["tournament_note"] = "NIT - First Round"
        assert season_projection._is_march_madness_game(event) is False


class TestLoadCachedTeamConference:
    def test_reads_team_conference_out_of_the_cached_object(self):
        raw_bucket = _raw_bucket({"12": "ACC"}, season=2026)

        result = season_projection._load_cached_team_conference(raw_bucket, 2026)

        assert result == {"12": "ACC"}
        raw_bucket.get_json.assert_called_once_with("ncaambb/conference-membership/2026.json")

    def test_no_known_season_skips_the_read_entirely(self):
        raw_bucket = _raw_bucket({"12": "ACC"})

        result = season_projection._load_cached_team_conference(raw_bucket, None)

        assert result == {}
        raw_bucket.object_exists.assert_not_called()

    def test_a_missing_cache_object_degrades_to_no_known_conferences(self):
        raw_bucket = _raw_bucket(None)

        result = season_projection._load_cached_team_conference(raw_bucket, 2026)

        assert result == {}

    def test_a_read_failure_degrades_to_no_known_conferences(self):
        raw_bucket = MagicMock()
        raw_bucket.object_exists.side_effect = Exception("S3 unreachable")

        result = season_projection._load_cached_team_conference(raw_bucket, 2026)

        assert result == {}


class TestSeasonStandingsInputs:
    def _storage(self, completed, scheduled):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {"completed": completed, "scheduled": scheduled}[status]
        return storage

    def test_derives_wins_losses_and_point_differential_from_completed_events(self):
        storage = self._storage([_completed_event("E1", 2026, "12", "24", 70, 60)], [])

        inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC", "24": "SEC"}))

        assert inputs["wins"]["12"] == 1
        assert inputs["losses"]["24"] == 1
        assert inputs["point_differential"]["12"] == 10
        assert inputs["point_differential"]["24"] == -10

    def test_conference_record_only_counts_conference_games(self):
        storage = self._storage(
            [
                _completed_event("E1", 2026, "12", "24", 70, 60, conference_competition=True),
                _completed_event("E2", 2026, "12", "9", 80, 50, conference_competition=False),
            ],
            [],
        )

        inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC", "24": "ACC", "9": "Big Ten"}))

        assert inputs["wins"]["12"] == 2
        assert inputs["conference_wins"]["12"] == 1  # only the ACC game counts

    def test_team_conference_comes_from_the_cached_object(self):
        storage = self._storage([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        raw_bucket = _raw_bucket({"12": "ACC"}, season=2026)

        inputs = season_projection._season_standings_inputs(storage, raw_bucket)

        raw_bucket.get_json.assert_called_once_with("ncaambb/conference-membership/2026.json")
        assert inputs["team_conference"] == {"12": "ACC"}

    def test_remaining_games_excludes_conference_tournament_and_march_madness_games(self):
        storage = self._storage(
            [],
            [
                _scheduled_event("E1", 2026, "2026-01-21", "12", "24"),
                _scheduled_event("E2", 2026, "2026-03-14", "12", "24", tournament_note="ACC Tournament"),
                _scheduled_event("E3", 2026, "2026-03-20", "12", "24", season_type=3, conference_competition=False, tournament_note="Men's Basketball Championship - South Region"),
            ],
        )

        inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC", "24": "ACC"}))

        assert inputs["remaining_games"] == [("12", "24", True)]

    def test_remaining_games_excludes_a_pairing_missing_a_known_conference(self):
        storage = self._storage([], [_scheduled_event("E1", 2026, "2026-01-21", "12", "7")])

        inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC"}))

        assert inputs["remaining_games"] == []
        assert inputs["team_next_event"]["12"] == "E1"


class TestCurrentRankings:
    def test_ranks_teams_by_ascending_score_lower_is_better(self):
        season_inputs = {
            "wins": {"a": 5, "b": 5, "c": 5}, "losses": {"a": 0, "b": 0, "c": 0},
            "current_ratings": {"a": 1500.0, "b": 1500.0, "c": 1500.0},
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        }
        with patch.object(season_projection, "_batch_score_teams", return_value={"a": 2.0, "b": 0.5, "c": 1.0}):
            rankings = season_projection._current_rankings(MagicMock(), _model_card(1), ["a", "b", "c"], season_inputs)

        assert rankings == {"b": 1, "c": 2, "a": 3}


class TestResolveMatchup:
    def test_a_bye_slot_is_always_projected_at_full_confidence(self):
        matchup = season_projection._resolve_matchup("t1", None, 1, None, {}, MagicMock(), MagicMock(), MagicMock(), {}, 0.0)

        assert matchup == {
            "status": "projected", "team_a": "t1", "seed_a": 1, "team_b": None, "seed_b": None,
            "predicted_winner": "t1", "win_probability": 1.0,
        }

    def test_no_real_game_yet_falls_back_to_the_models_own_projection(self):
        matchup = season_projection._resolve_matchup(
            "t1", "t2", 1, 2, {}, MagicMock(), MagicMock(), MagicMock(),
            {"t1": 1600.0, "t2": 1400.0}, 0.0,
        )

        assert matchup["status"] == "projected"
        assert matchup["predicted_winner"] == "t1"

    def test_a_completed_real_game_reports_the_actual_result(self):
        real_event = _completed_event("E1", 2026, "t1", "t2", 70, 60, tournament_note="ACC Tournament")
        predictions_table = MagicMock()
        predictions_table.query.return_value = []

        matchup = season_projection._resolve_matchup(
            "t1", "t2", 1, 2, {frozenset({"t1", "t2"}): real_event}, MagicMock(), MagicMock(), predictions_table,
            {}, 0.0,
        )

        assert matchup["status"] == "final"
        assert matchup["actual_winner"] == "t1"
        assert matchup["actual_home_score"] == 70


class TestScheduledSeasonProjection:
    """GET /ncaambb/season is served from predict-read/handler.py, not
    here -- this Lambda instead computes the projection on Terraform/
    scheduler-ncaambb-season-projection.tf's own direct EventBridge
    Scheduler invoke and writes it to S3."""

    def _rig(self, completed, scheduled):
        ncaambb_predict._storage = MagicMock()
        ncaambb_predict._model_bucket = MagicMock()
        ncaambb_predict._predictions_table = MagicMock()
        ncaambb_predict._predictions_table.query.return_value = []
        ncaambb_predict._storage.get_all_events.side_effect = lambda sport, status: {"completed": completed, "scheduled": scheduled}[status]

    def test_writes_the_season_projection_to_s3_under_the_expected_key(self):
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket({"12": "ACC", "24": "ACC"})

        with patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("nope")):
            response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        ncaambb_predict._model_bucket.put_json.assert_called_once()
        key, body = ncaambb_predict._model_bucket.put_json.call_args[0]
        assert key == "season-projections/ncaambb/latest.json"
        assert body["season"] == 2026
        assert body["standings"][0]["wins"] == 1
        assert body["standings"][0]["conference"] == "ACC"
        # Team outcomes only -- no player-prop leaderboard.
        assert "leaderboards" not in body

    def test_no_tracked_teams_skips_simulation_but_still_writes_an_empty_projection(self):
        self._rig([], [])
        ncaambb_predict._raw_bucket = _raw_bucket(None)

        response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"] == []
        assert body["conference_brackets"] == []
        assert body["march_madness_bracket"] is None

    def test_a_missing_conference_cache_writes_an_empty_standings_this_run(self):
        # A brand-new season the cache hasn't caught up to yet -- standings
        # are derived from team_conference's own keys (same "no known
        # conference simply excludes a team" precedent NCAAFB's own
        # season_projection.py already accepts), so a fully-missing cache
        # degrades to no standings at all for this one run rather than
        # crashing, self-correcting the next time schedule-sync writes it.
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket(None)

        response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"] == []

    def test_no_promoted_ranking_model_skips_simulation_but_still_writes_standings(self):
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket({"12": "ACC", "24": "ACC"})

        with patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("nope")):
            response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        assert "projected_wins" not in body["standings"][0]
        assert body["conference_brackets"] == []
        assert body["march_madness_bracket"] is None

    def test_simulation_and_brackets_run_when_a_ranking_model_is_promoted(self):
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket({"12": "ACC", "24": "ACC"})

        simulated = {"12": {"projected_wins": 20.0, "national_champion_probability": 0.01}, "24": {"projected_wins": 15.0, "national_champion_probability": 0.0}}

        with patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(season_simulation, "simulate_season", return_value=simulated) as simulate_season, \
             patch.object(season_projection, "_batch_score_teams", return_value={"12": 0.0, "24": 1.0}), \
             patch.object(season_projection, "_conference_bracket_payloads", return_value=[{"conference": "ACC", "bracket": {"rounds": [], "champion": "12"}}]) as conference_brackets, \
             patch.object(season_projection, "_march_madness_bracket_payload", return_value={"rounds": [], "champion": "12"}) as march_madness:
            response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        simulate_season.assert_called_once()
        conference_brackets.assert_called_once()
        march_madness.assert_called_once()
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"][0]["projected_wins"] == 20.0
        assert body["conference_brackets"] == [{"conference": "ACC", "bracket": {"rounds": [], "champion": "12"}}]
        assert body["march_madness_bracket"] == {"rounds": [], "champion": "12"}

    def test_a_current_rankings_failure_does_not_lose_the_rest_of_the_run(self):
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket({"12": "ACC", "24": "ACC"})
        simulated = {"12": {"projected_wins": 20.0}, "24": {"projected_wins": 15.0}}

        with patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(season_simulation, "simulate_season", return_value=simulated), \
             patch.object(season_projection, "_current_rankings", side_effect=KeyError("boom")), \
             patch.object(season_projection, "_conference_bracket_payloads", return_value=[]), \
             patch.object(season_projection, "_march_madness_bracket_payload", return_value=None):
            response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"][0]["projected_wins"] == 20.0
        assert body["standings"][0]["current_rank"] is None
