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
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

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


class TestCurrentNcaambbSeason:
    """Mirrors schedule-sync/handler.py's own TestCurrentNcaambbSeason --
    both Lambdas' copies of this heuristic must stay identical (see
    season_projection.py's own docstring on why it's duplicated, not
    imported, plus what it'd cost these two Lambdas to drift out of
    sync)."""

    def test_before_august_uses_the_current_calendar_year(self):
        assert season_projection._current_ncaambb_season(date(2026, 3, 15)) == 2026

    def test_august_or_later_uses_next_calendar_year(self):
        assert season_projection._current_ncaambb_season(date(2026, 8, 22)) == 2027

    def test_december_uses_next_calendar_year(self):
        assert season_projection._current_ncaambb_season(date(2026, 12, 1)) == 2027


class TestSeasonStandingsInputs:
    def _storage(self, completed, scheduled):
        storage = MagicMock()
        storage.get_all_events.side_effect = lambda sport, status: {"completed": completed, "scheduled": scheduled}[status]
        return storage

    def _patched_season(self, season):
        return patch.object(season_projection, "_current_ncaambb_season", return_value=season)

    def test_derives_wins_losses_and_point_differential_from_completed_events(self):
        storage = self._storage([_completed_event("E1", 2026, "12", "24", 70, 60)], [])

        with self._patched_season(2026):
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

        with self._patched_season(2026):
            inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC", "24": "ACC", "9": "Big Ten"}))

        assert inputs["wins"]["12"] == 2
        assert inputs["conference_wins"]["12"] == 1  # only the ACC game counts

    def test_team_conference_comes_from_the_cached_object(self):
        storage = self._storage([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        raw_bucket = _raw_bucket({"12": "ACC"}, season=2026)

        with self._patched_season(2026):
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

        with self._patched_season(2026):
            inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC", "24": "ACC"}))

        assert inputs["remaining_games"] == [("12", "24", True)]

    def test_remaining_games_excludes_a_pairing_missing_a_known_conference(self):
        storage = self._storage([], [_scheduled_event("E1", 2026, "2026-01-21", "12", "7")])

        with self._patched_season(2026):
            inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC"}))

        assert inputs["remaining_games"] == []
        assert inputs["team_next_event"]["12"] == "E1"

    def test_current_season_is_the_calendar_heuristic_regardless_of_what_events_exist(self):
        # Regression, two different bugs found live at different points:
        # deriving current_season from event data at all (rather than the
        # fixed calendar heuristic schedule-sync's own cache key already
        # uses) either flips forward the moment next season's games start
        # getting pre-seeded months early (every team showing 0-0), or --
        # an earlier version of this same fix -- freezes on a season
        # that's already over for the whole off-season instead of
        # forward-looking to the upcoming one. Neither completed nor
        # scheduled events drive this decision at all now.
        storage = self._storage(
            [_completed_event("E1", 2025, "12", "24", 70, 60)],
            [_scheduled_event("E2", 2026, "2026-11-10", "12", "24")],
        )

        with self._patched_season(2027):
            inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC"}, season=2027))

        assert inputs["current_season"] == 2027
        assert inputs["wins"] == {}  # neither event above is season 2027 -- correctly 0-0, not a bug
        assert inputs["scheduled"] == []
        assert inputs["completed"] == []

    def test_events_are_filtered_to_only_the_resolved_seasons_games(self):
        storage = self._storage(
            [_completed_event("E1", 2026, "12", "24", 70, 60), _completed_event("E2", 2025, "12", "24", 50, 40)],
            [],
        )

        with self._patched_season(2026):
            inputs = season_projection._season_standings_inputs(storage, _raw_bucket({"12": "ACC", "24": "ACC"}))

        assert [e["event_key"] for e in inputs["completed"]] == ["E1"]


class TestMarchMadnessBracketPayload:
    """No real postseason games logged (storage.get_all_events returns
    []), so every matchup resolves through the deterministic "projected"
    branch -- these tests are about the payload's own shape (regions kept
    separate, not flattened into one round list), not the reconciliation
    logic itself (see TestResolveMatchup for that)."""

    def _teams(self, n):
        return [str(i) for i in range(1, n + 1)]

    def _season_inputs(self, teams):
        ratings = {team_id: 1500.0 for team_id in teams}
        return {
            "current_ratings": ratings, "wins": {}, "losses": {},
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        }

    def test_regions_are_kept_separate_not_flattened_into_one_round_list(self):
        teams = self._teams(80)
        conference_champions = {f"conf{i}": teams[i] for i in range(10)}
        model_scores = {team_id: float(i) for i, team_id in enumerate(teams)}  # lower is better; "1" is the top overall seed
        storage = MagicMock()
        storage.get_all_events.return_value = []

        with patch.object(season_projection, "_current_model_scores", return_value=model_scores):
            bracket = season_projection._march_madness_bracket_payload(
                storage, MagicMock(), MagicMock(), self._season_inputs(teams), 2026,
                MagicMock(), _model_card(1), teams, conference_champions,
            )

        assert "rounds" not in bracket
        assert set(bracket["regions"]) == set(season_simulation.REGION_NAMES)
        for region in bracket["regions"].values():
            assert [r["round"] for r in region["rounds"]] == season_simulation.MARCH_MADNESS_REGION_ROUND_NAMES
            assert region["champion"] is not None
        assert len(bracket["first_four"]) == 4
        assert len(bracket["final_four"]) == 2
        assert bracket["championship"]["team_a"] is not None
        assert bracket["championship"]["team_b"] is not None
        assert bracket["champion"] in teams

    def test_champion_is_the_top_overall_seed_when_nothing_upsets(self):
        # Every matchup resolves by Elo alone here (no real games) --
        # rating strictly decreasing with seed (unlike _season_inputs'
        # own all-equal default) means no game is a 50/50 coin flip, so
        # the top overall seed should carry all the way to the champion.
        teams = self._teams(80)
        conference_champions = {f"conf{i}": teams[i] for i in range(10)}
        model_scores = {team_id: float(i) for i, team_id in enumerate(teams)}
        season_inputs = self._season_inputs(teams)
        season_inputs["current_ratings"] = {team_id: 2000.0 - i for i, team_id in enumerate(teams)}
        storage = MagicMock()
        storage.get_all_events.return_value = []

        with patch.object(season_projection, "_current_model_scores", return_value=model_scores):
            bracket = season_projection._march_madness_bracket_payload(
                storage, MagicMock(), MagicMock(), season_inputs, 2026,
                MagicMock(), _model_card(1), teams, conference_champions,
            )

        assert bracket["champion"] == "1"  # _teams() is 1-indexed; teams[0] == "1" has the best score/rating

    def test_returns_none_with_fewer_than_2_conference_champions(self):
        result = season_projection._march_madness_bracket_payload(
            MagicMock(), MagicMock(), MagicMock(), self._season_inputs(["1"]), 2026,
            MagicMock(), _model_card(1), ["1"], {"conf0": "1"},
        )

        assert result is None


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

    @pytest.fixture(autouse=True)
    def fixed_current_season(self):
        """Every fixture in this class hardcodes season=2026 -- pins
        _current_ncaambb_season so these tests don't depend on (and don't
        flake around) the real current date, same reasoning as
        TestSeasonStandingsInputs' own _patched_season."""
        with patch.object(season_projection, "_current_ncaambb_season", return_value=2026):
            yield

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
