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
whose _reset_ncaambb_singletons fixture (autouse) resets
ncaambb_predict._storage/_model_bucket/_predictions_table/_raw_bucket
before and after every test here.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from library.serving import model_loader
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
    write failure). list_keys defaults to empty -- no cached AP poll --
    since get_json's own return_value is already claimed by the
    conference-membership object above; tests exercising the real
    current-rank path set list_keys/get_json up themselves."""
    bucket = MagicMock()
    bucket.list_keys.return_value = []
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


class TestRecordGameResult:
    def _dicts(self):
        return ({}, {}, {}, {}, {}, {}, {})

    def test_a_missing_score_on_either_side_is_a_no_op(self):
        event = {
            "event_date": "2026-01-14",
            "participants": [
                {"entity_id": "12", "result": None},
                {"entity_id": "24", "result": {"score": 60, "won": False}},
            ],
        }
        wins, losses, point_differential, team_last_completed_date, conference_wins, conference_losses = self._dicts()[:6]

        season_projection._record_game_result(
            event, "12", "24", True, wins, losses, point_differential,
            conference_wins, conference_losses, team_last_completed_date,
        )

        assert wins == {}
        assert losses == {}
        assert point_differential == {}
        assert team_last_completed_date == {}

    def test_a_non_conference_game_does_not_touch_conference_records(self):
        event = {
            "event_date": "2026-01-14",
            "participants": [
                {"entity_id": "12", "result": {"score": 70, "won": True}},
                {"entity_id": "24", "result": {"score": 60, "won": False}},
            ],
        }
        wins, losses, point_differential, team_last_completed_date, conference_wins, conference_losses = self._dicts()[:6]

        season_projection._record_game_result(
            event, "12", "24", False, wins, losses, point_differential,
            conference_wins, conference_losses, team_last_completed_date,
        )

        assert wins["12"] == 1
        assert conference_wins == {}
        assert conference_losses == {}


class TestCompletedGameRecords:
    def test_a_malformed_event_with_no_home_away_roles_is_skipped(self):
        event = {"event_key": "E1", "event_date": "2026-01-14", "participants": [{"entity_id": "12", "role": "unknown"}]}

        wins, losses, point_differential, team_last_completed_date, conference_wins, conference_losses, completed_by_team = (
            season_projection._completed_game_records([event])
        )

        assert wins == {}
        assert losses == {}
        assert completed_by_team == {}


class TestRemainingGameInputs:
    def test_a_malformed_event_with_no_home_away_roles_is_skipped(self):
        event = {"event_key": "E1", "event_date": "2026-01-21", "participants": [{"entity_id": "12", "role": "unknown"}]}

        remaining_games, team_next_event = season_projection._remaining_game_inputs(
            [event], {"12": "ACC", "24": "ACC"},
        )

        assert remaining_games == []
        assert team_next_event == {}


class TestRankingFeatureRow:
    def test_derives_games_played_and_pulls_rolling_stats_from_season_inputs(self):
        season_inputs = {
            "avg_points_scored": {"12": 75.0}, "avg_points_allowed": {"12": 65.0},
            "win_streak": {"12": 3}, "strength_of_schedule": {"12": 1550.0},
        }

        row = season_projection._ranking_feature_row("12", {"12": 10}, {"12": 5}, {"12": 1600.0}, season_inputs)

        assert row == {
            "elo": 1600.0, "wins": 10, "losses": 5, "games_played": 15,
            "avg_points_scored": 75.0, "avg_points_allowed": 65.0,
            "win_streak": 3, "strength_of_schedule": 1550.0,
        }

    def test_a_team_with_no_rolling_stats_yet_defaults_to_none_or_zero(self):
        row = season_projection._ranking_feature_row("12", {}, {}, {}, {
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        })

        assert row["games_played"] == 0
        assert row["win_streak"] == 0
        assert row["avg_points_scored"] is None
        assert row["strength_of_schedule"] is None


class TestBatchScoreTeams:
    def test_scores_every_team_in_one_batched_predict_call(self):
        fake_adapter = MagicMock()
        fake_adapter.predict.return_value = [0.2, 0.8]
        model_card = {"algorithm": "fake", "feature_columns": ["elo", "wins"]}
        season_inputs = {
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        }

        with patch.object(season_projection, "ADAPTERS", {"fake": fake_adapter}):
            result = season_projection._batch_score_teams(
                MagicMock(), model_card, ["12", "24"], season_inputs,
                {"12": 10, "24": 5}, {"12": 5, "24": 10}, {"12": 1600.0, "24": 1500.0},
            )

        assert result == {"12": 0.2, "24": 0.8}
        fake_adapter.predict.assert_called_once()

    def test_a_missing_feature_value_becomes_nan_not_a_crash(self):
        fake_adapter = MagicMock()
        fake_adapter.predict.return_value = [0.5]
        model_card = {"algorithm": "fake", "feature_columns": ["strength_of_schedule"]}
        season_inputs = {
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        }

        with patch.object(season_projection, "ADAPTERS", {"fake": fake_adapter}):
            result = season_projection._batch_score_teams(
                MagicMock(), model_card, ["12"], season_inputs, {}, {}, {},
            )

        assert result == {"12": 0.5}
        X = fake_adapter.predict.call_args.args[1]
        assert X["strength_of_schedule"].isna().all()


class TestConferenceBracketPayloads:
    """_reconcile_single_elim_bracket's own reconciliation logic is
    covered by TestResolveMatchup/TestScheduledMatchupRow -- these tests
    are about _conference_bracket_payloads' own orchestration around it
    (grouping by conference, skipping a conference with <2 tracked
    members, and one conference's bracket build failing without losing
    the others), so that machinery is mocked out here."""

    def _season_inputs(self, team_conference):
        return {
            "team_conference": team_conference, "conference_wins": {}, "point_differential": {},
            "current_ratings": {team_id: 1500.0 for team_id in team_conference},
        }

    def test_builds_one_bracket_per_conference_with_at_least_2_members(self):
        team_conference = {"12": "ACC", "24": "ACC", "9": "Big Ten", "7": "Big Ten"}
        storage = MagicMock()
        storage.get_all_events.return_value = []

        with patch.object(
            season_projection, "_reconcile_single_elim_bracket",
            side_effect=lambda seed_order, *a, **kw: {"rounds": [], "champion": seed_order[0]},
        ):
            payloads = season_projection._conference_bracket_payloads(
                storage, MagicMock(), MagicMock(), self._season_inputs(team_conference), 2026,
            )

        assert {p["conference"] for p in payloads} == {"ACC", "Big Ten"}

    def test_a_conference_with_only_one_tracked_member_is_skipped(self):
        team_conference = {"12": "ACC", "9": "Big Ten", "7": "Big Ten"}
        storage = MagicMock()
        storage.get_all_events.return_value = []

        with patch.object(
            season_projection, "_reconcile_single_elim_bracket",
            side_effect=lambda seed_order, *a, **kw: {"rounds": [], "champion": seed_order[0]},
        ):
            payloads = season_projection._conference_bracket_payloads(
                storage, MagicMock(), MagicMock(), self._season_inputs(team_conference), 2026,
            )

        assert {p["conference"] for p in payloads} == {"Big Ten"}

    def test_one_conferences_bracket_build_failing_does_not_lose_the_others(self):
        team_conference = {"12": "ACC", "24": "ACC", "9": "Big Ten", "7": "Big Ten"}
        storage = MagicMock()
        storage.get_all_events.return_value = []

        def _reconcile(seed_order, round_names, real_matchups, storage, s3, predictions_table, ratings, home_advantage):
            if "12" in seed_order:  # ACC's own bracket build fails; Big Ten's does not
                raise Exception("model unavailable")
            return {"rounds": [], "champion": seed_order[0]}

        with patch.object(season_projection, "_reconcile_single_elim_bracket", side_effect=_reconcile):
            payloads = season_projection._conference_bracket_payloads(
                storage, MagicMock(), MagicMock(), self._season_inputs(team_conference), 2026,
            )

        assert {p["conference"] for p in payloads} == {"Big Ten"}

    def test_no_conferences_with_2_plus_members_returns_an_empty_list(self):
        payloads = season_projection._conference_bracket_payloads(
            MagicMock(), MagicMock(), MagicMock(), self._season_inputs({"12": "ACC"}), 2026,
        )

        assert payloads == []


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

    def test_a_region_brackets_build_failing_returns_none_for_the_whole_bracket(self):
        # Unlike a conference bracket (one failure just skips that one
        # conference), a failed region here can't be silently dropped --
        # March Madness has exactly 4 regions by construction, so a
        # missing one means there's no real bracket to show at all.
        teams = self._teams(80)
        conference_champions = {f"conf{i}": teams[i] for i in range(10)}
        model_scores = {team_id: float(i) for i, team_id in enumerate(teams)}
        storage = MagicMock()
        storage.get_all_events.return_value = []

        with patch.object(season_projection, "_current_model_scores", return_value=model_scores), \
             patch.object(season_projection, "_reconcile_single_elim_bracket", side_effect=Exception("model unavailable")):
            bracket = season_projection._march_madness_bracket_payload(
                storage, MagicMock(), MagicMock(), self._season_inputs(teams), 2026,
                MagicMock(), _model_card(1), teams, conference_champions,
            )

        assert bracket is None

    def test_returns_none_with_fewer_than_2_conference_champions(self):
        result = season_projection._march_madness_bracket_payload(
            MagicMock(), MagicMock(), MagicMock(), self._season_inputs(["1"]), 2026,
            MagicMock(), _model_card(1), ["1"], {"conf0": "1"},
        )

        assert result is None


class TestModelRankings:
    def test_ranks_teams_by_ascending_score_lower_is_better(self):
        season_inputs = {
            "wins": {"a": 5, "b": 5, "c": 5}, "losses": {"a": 0, "b": 0, "c": 0},
            "current_ratings": {"a": 1500.0, "b": 1500.0, "c": 1500.0},
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        }
        with patch.object(season_projection, "_batch_score_teams", return_value={"a": 2.0, "b": 0.5, "c": 1.0}):
            rankings = season_projection._model_rankings(MagicMock(), _model_card(1), ["a", "b", "c"], season_inputs)

        assert rankings == {"b": 1, "c": 2, "a": 3}


class TestLatestApPollRanks:
    def test_no_season_returns_empty(self):
        assert season_projection._latest_ap_poll_ranks(MagicMock(), None) == {}

    def test_no_cached_poll_returns_empty(self):
        bucket = MagicMock()
        bucket.list_keys.return_value = []

        assert season_projection._latest_ap_poll_ranks(bucket, 2026) == {}

    def test_picks_the_highest_week_within_the_same_season_type(self):
        bucket = MagicMock()
        bucket.list_keys.return_value = ["ncaambb/rankings/2026/2/3.json", "ncaambb/rankings/2026/2/10.json"]
        bucket.get_json.return_value = {"ranks": [{"team": {"$ref": ".../teams/12?lang=en"}, "current": 1}]}

        result = season_projection._latest_ap_poll_ranks(bucket, 2026)

        bucket.get_json.assert_called_once_with("ncaambb/rankings/2026/2/10.json")
        assert result == {"12": 1}

    def test_a_postseason_type_outranks_any_regular_season_week(self):
        bucket = MagicMock()
        bucket.list_keys.return_value = ["ncaambb/rankings/2026/2/18.json", "ncaambb/rankings/2026/3/1.json"]
        bucket.get_json.return_value = {"ranks": []}

        season_projection._latest_ap_poll_ranks(bucket, 2026)

        bucket.get_json.assert_called_once_with("ncaambb/rankings/2026/3/1.json")

    def test_a_key_not_matching_the_expected_shape_is_skipped(self):
        # A stray object under the same S3 prefix (or a future naming
        # change) shouldn't crash the regex match -- just be ignored in
        # favor of whatever real weekly poll keys are also present.
        bucket = MagicMock()
        bucket.list_keys.return_value = ["ncaambb/rankings/2026/not-a-real-key.json", "ncaambb/rankings/2026/2/5.json"]
        bucket.get_json.return_value = {"ranks": []}

        season_projection._latest_ap_poll_ranks(bucket, 2026)

        bucket.get_json.assert_called_once_with("ncaambb/rankings/2026/2/5.json")


class TestRealPostseasonMatchups:
    def test_includes_both_scheduled_and_completed_games_matching_the_predicate_and_season(self):
        storage = MagicMock()
        completed = _completed_event("E1", 2026, "12", "24", 70, 60, tournament_note="ACC Tournament")
        scheduled = _scheduled_event("E2", 2026, "2026-03-14", "9", "7", tournament_note="Big Ten Tournament")
        storage.get_all_events.side_effect = lambda sport, status: {"completed": [completed], "scheduled": [scheduled]}[status]

        result = season_projection._real_postseason_matchups(storage, 2026, season_projection._is_conference_tournament_game)

        assert result[frozenset({"12", "24"})] == completed
        assert result[frozenset({"9", "7"})] == scheduled

    def test_excludes_events_from_a_different_season(self):
        storage = MagicMock()
        other_season = _completed_event("E1", 2025, "12", "24", 70, 60, tournament_note="ACC Tournament")
        storage.get_all_events.side_effect = lambda sport, status: {"completed": [other_season], "scheduled": []}[status]

        result = season_projection._real_postseason_matchups(storage, 2026, season_projection._is_conference_tournament_game)

        assert result == {}

    def test_excludes_events_the_predicate_rejects(self):
        storage = MagicMock()
        regular_season = _completed_event("E1", 2026, "12", "24", 70, 60)  # no tournament_note
        storage.get_all_events.side_effect = lambda sport, status: {"completed": [regular_season], "scheduled": []}[status]

        result = season_projection._real_postseason_matchups(storage, 2026, season_projection._is_conference_tournament_game)

        assert result == {}

    def test_a_malformed_event_with_no_home_away_roles_is_skipped(self):
        storage = MagicMock()
        malformed = {
            "event_key": "E1", "season": 2026, "season_type": 2, "conference_competition": True,
            "tournament_note": "ACC Tournament", "participants": [{"entity_id": "12", "role": "unknown"}],
        }
        storage.get_all_events.side_effect = lambda sport, status: {"completed": [malformed], "scheduled": []}[status]

        result = season_projection._real_postseason_matchups(storage, 2026, season_projection._is_conference_tournament_game)

        assert result == {}


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

    def test_a_real_but_not_yet_played_game_delegates_to_the_scheduled_row(self):
        real_event = _scheduled_event("E1", 2026, "2026-03-14", "t1", "t2", tournament_note="ACC Tournament")
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        storage = MagicMock()
        s3 = MagicMock()

        with patch.object(season_projection, "event_prediction") as mock_event_prediction:
            matchup = season_projection._resolve_matchup(
                "t1", "t2", 1, 2, {frozenset({"t1", "t2"}): real_event}, storage, s3, predictions_table,
                {}, 0.0,
            )

        assert matchup["status"] == "scheduled"
        assert matchup["team_a"] == "t1"
        assert matchup["team_b"] == "t2"
        mock_event_prediction.compute_and_cache_event.assert_called_once()


class TestPredictedWinnerAndProbability:
    def test_nothing_logged_returns_none_and_none(self):
        result = season_projection._predicted_winner_and_probability(None, "home", "away")
        assert result == (None, None)

    def test_home_favored_returns_home_and_its_own_probability(self):
        result = season_projection._predicted_winner_and_probability(
            {"home_win_probability": 0.7}, "home", "away",
        )
        assert result == ("home", 0.7)

    def test_away_favored_returns_away_and_the_complementary_probability(self):
        result = season_projection._predicted_winner_and_probability(
            {"home_win_probability": 0.3}, "home", "away",
        )
        assert result == ("away", 0.7)


class TestScheduledMatchupRow:
    def test_an_already_logged_prediction_is_used_without_recomputing(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = [
            {"model_key": "MODEL#win-probability#v1", "generated_at": "2026-01-01T00:00:00+00:00",
             "predicted_value": {"home_win_probability": 0.6}},
        ]
        real_event = _scheduled_event("E1", 2026, "2026-03-14", "t1", "t2", tournament_note="ACC Tournament")

        with patch.object(season_projection, "event_prediction") as mock_event_prediction:
            row = season_projection._scheduled_matchup_row(
                real_event, "E1", "t1", "t2", 1, 2, MagicMock(), MagicMock(), predictions_table,
            )

        assert row == {
            "status": "scheduled", "team_a": "t1", "team_b": "t2", "seed_a": 1, "seed_b": 2,
            "predicted_winner": "t1", "win_probability": 0.6,
        }
        mock_event_prediction.compute_and_cache_event.assert_not_called()

    def test_nothing_logged_yet_computes_and_caches_then_re_reads(self):
        predictions_table = MagicMock()
        predictions_table.query.side_effect = [
            [],
            [{"model_key": "MODEL#win-probability#v1", "generated_at": "2026-01-01T00:00:00+00:00",
              "predicted_value": {"home_win_probability": 0.6}}],
        ]
        real_event = _scheduled_event("E1", 2026, "2026-03-14", "t1", "t2", tournament_note="ACC Tournament", event_id="401")
        storage = MagicMock()
        s3 = MagicMock()

        with patch.object(season_projection, "event_prediction") as mock_event_prediction:
            row = season_projection._scheduled_matchup_row(real_event, "E1", "t1", "t2", 1, 2, storage, s3, predictions_table)

        mock_event_prediction.compute_and_cache_event.assert_called_once_with(storage, s3, predictions_table, "401")
        assert row["predicted_winner"] == "t1"
        assert row["win_probability"] == 0.6

    def test_a_failed_live_computation_still_returns_a_valid_row_with_no_prediction(self):
        predictions_table = MagicMock()
        predictions_table.query.return_value = []
        real_event = _scheduled_event("E1", 2026, "2026-03-14", "t1", "t2", tournament_note="ACC Tournament")

        with patch.object(season_projection, "event_prediction") as mock_event_prediction:
            mock_event_prediction.compute_and_cache_event.side_effect = Exception("model load failed")
            row = season_projection._scheduled_matchup_row(
                real_event, "E1", "t1", "t2", 1, 2, MagicMock(), MagicMock(), predictions_table,
            )

        assert row["status"] == "scheduled"
        assert row["predicted_winner"] is None
        assert row["win_probability"] is None


class TestComputeProjectionsAndBrackets:
    """Each of the 4 pieces (simulation, model rankings, conference
    brackets, March Madness bracket) is independently try/excepted --
    TestScheduledSeasonProjection's own end-to-end tests always mock every
    piece to a clean success, so the exception branches (and the
    score_teams closure passed into simulate_season) are only reachable
    by calling this function directly."""

    def _season_inputs(self):
        return {
            "wins": {"12": 5}, "losses": {"12": 3}, "conference_wins": {"12": 3}, "conference_losses": {"12": 1},
            "point_differential": {"12": 20}, "remaining_games": [], "current_ratings": {"12": 1550.0},
            "team_conference": {"12": "ACC"},
            "avg_points_scored": {}, "avg_points_allowed": {}, "win_streak": {}, "strength_of_schedule": {},
        }

    def test_score_teams_closure_delegates_to_batch_score_teams(self):
        # simulate_season calls its own score_teams callback with whatever
        # simulated wins/losses/ratings that Monte Carlo iteration has --
        # confirms the closure forwards them (plus the fixed estimator/
        # model_card/teams/season_inputs) rather than silently dropping one.
        model_card = _model_card(1)
        season_inputs = self._season_inputs()

        def fake_simulate_season(wins, losses, conference_wins, conference_losses, point_differential, remaining_games, ratings, team_conference, score_teams):
            return {"12": {"projected_wins": score_teams({"12": 1}, {"12": 0}, {"12": 1600.0})["12"]}}

        with patch.object(season_simulation, "simulate_season", side_effect=fake_simulate_season), \
             patch.object(season_projection, "_batch_score_teams", return_value={"12": 0.5}) as mock_batch_score, \
             patch.object(season_projection, "_model_rankings", return_value={}), \
             patch.object(season_projection, "_conference_bracket_payloads", return_value=[]), \
             patch.object(season_projection, "_march_madness_bracket_payload", return_value=None):
            simulation, _, _, _ = season_projection._compute_projections_and_brackets(
                MagicMock(), model_card, ["12"], season_inputs, MagicMock(), MagicMock(), MagicMock(), 2026,
            )

        assert simulation == {"12": {"projected_wins": 0.5}}
        mock_batch_score.assert_called_once()
        assert mock_batch_score.call_args.args[1] is model_card
        assert mock_batch_score.call_args.args[2] == ["12"]
        assert mock_batch_score.call_args.args[3] is season_inputs
        assert mock_batch_score.call_args.args[4] == {"12": 1}  # the simulated wins score_teams was called with
        assert mock_batch_score.call_args.args[5] == {"12": 0}  # the simulated losses
        assert mock_batch_score.call_args.args[6] == {"12": 1600.0}  # the simulated ratings

    def test_simulation_failure_leaves_it_empty_but_still_computes_the_rest(self):
        with patch.object(season_simulation, "simulate_season", side_effect=Exception("boom")), \
             patch.object(season_projection, "_model_rankings", return_value={"12": 1}), \
             patch.object(season_projection, "_conference_bracket_payloads", return_value=[{"conference": "ACC", "bracket": {"champion": "12"}}]), \
             patch.object(season_projection, "_march_madness_bracket_payload", return_value={"champion": "12"}):
            simulation, model_rankings, conference_brackets, march_madness = season_projection._compute_projections_and_brackets(
                MagicMock(), _model_card(1), ["12"], self._season_inputs(), MagicMock(), MagicMock(), MagicMock(), 2026,
            )

        assert simulation == {}
        assert model_rankings == {"12": 1}
        assert conference_brackets == [{"conference": "ACC", "bracket": {"champion": "12"}}]
        assert march_madness == {"champion": "12"}

    def test_conference_brackets_failure_leaves_it_empty_but_still_computes_the_rest(self):
        with patch.object(season_simulation, "simulate_season", return_value={"12": {"projected_wins": 8.0}}), \
             patch.object(season_projection, "_model_rankings", return_value={"12": 1}), \
             patch.object(season_projection, "_conference_bracket_payloads", side_effect=Exception("boom")), \
             patch.object(season_projection, "_march_madness_bracket_payload", return_value={"champion": "12"}):
            simulation, model_rankings, conference_brackets, march_madness = season_projection._compute_projections_and_brackets(
                MagicMock(), _model_card(1), ["12"], self._season_inputs(), MagicMock(), MagicMock(), MagicMock(), 2026,
            )

        assert simulation == {"12": {"projected_wins": 8.0}}
        assert conference_brackets == []
        assert march_madness == {"champion": "12"}

    def test_march_madness_failure_leaves_it_none_but_still_computes_the_rest(self):
        with patch.object(season_simulation, "simulate_season", return_value={"12": {"projected_wins": 8.0}}), \
             patch.object(season_projection, "_model_rankings", return_value={"12": 1}), \
             patch.object(season_projection, "_conference_bracket_payloads", return_value=[{"conference": "ACC", "bracket": {"champion": "12"}}]), \
             patch.object(season_projection, "_march_madness_bracket_payload", side_effect=Exception("boom")):
            simulation, model_rankings, conference_brackets, march_madness = season_projection._compute_projections_and_brackets(
                MagicMock(), _model_card(1), ["12"], self._season_inputs(), MagicMock(), MagicMock(), MagicMock(), 2026,
            )

        assert simulation == {"12": {"projected_wins": 8.0}}
        assert conference_brackets == [{"conference": "ACC", "bracket": {"champion": "12"}}]
        assert march_madness is None


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

    def test_current_rank_comes_from_the_cached_ap_poll_not_the_model(self):
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket({"12": "ACC", "24": "ACC"})
        ncaambb_predict._raw_bucket.list_keys.return_value = ["ncaambb/rankings/2026/2/5.json"]
        ncaambb_predict._raw_bucket.get_json.side_effect = lambda key: {
            "ncaambb/conference-membership/2026.json": {"season": 2026, "team_conference": {"12": "ACC", "24": "ACC"}},
            "ncaambb/rankings/2026/2/5.json": {"ranks": [{"team": {"$ref": ".../teams/12?lang=en"}, "current": 4}]},
        }[key]

        with patch.object(model_loader, "load_current_model", side_effect=model_loader.NoPromotedModelError("nope")):
            response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        by_team = {row["team_id"]: row for row in body["standings"]}
        assert by_team["12"]["current_rank"] == 4
        assert by_team["24"]["current_rank"] is None
        # No promoted model this run -- the model's own opinion is absent,
        # independent of the real rank above.
        assert by_team["12"]["model_rank"] is None

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

    def test_a_model_rankings_failure_does_not_lose_the_rest_of_the_run(self):
        self._rig([_completed_event("E1", 2026, "12", "24", 70, 60)], [])
        ncaambb_predict._raw_bucket = _raw_bucket({"12": "ACC", "24": "ACC"})
        simulated = {"12": {"projected_wins": 20.0}, "24": {"projected_wins": 15.0}}

        with patch.object(model_loader, "load_current_model", return_value=(MagicMock(), _model_card(1))), \
             patch.object(season_simulation, "simulate_season", return_value=simulated), \
             patch.object(season_projection, "_model_rankings", side_effect=KeyError("boom")), \
             patch.object(season_projection, "_conference_bracket_payloads", return_value=[]), \
             patch.object(season_projection, "_march_madness_bracket_payload", return_value=None):
            response = ncaambb_predict.lambda_handler({"detail-type": "ScheduledSeasonProjection"}, None)

        assert response == {"status": "ok"}
        body = ncaambb_predict._model_bucket.put_json.call_args[0][1]
        assert body["standings"][0]["projected_wins"] == 20.0
        assert body["standings"][0]["model_rank"] is None
