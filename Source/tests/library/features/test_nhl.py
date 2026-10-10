"""
Unit tests for library.features.nhl -- records, rolling team features,
schedule/context, lineup, goalie features and the two row builders, with
hand-computed expected values. No AWS involved. Uses real NHL team ids
(BOS=1, NYR=13, NYI=12, LA=8, VAN=22).
"""
import pytest

from library.features import nhl

from _nhl_test_helpers import event as _event, goalie as _goalie, skater as _skater, team_box as _team_box

BOS, NYR, NYI, LA, VAN = "1", "13", "12", "8", "22"


def _record(event, team_id, own_box=None, opponent_box=None):
    return nhl.team_game_record(event, team_id, own_box, opponent_box)


def _plain_record(**fields):
    """A bare record for rolling tests -- only the fields a test names."""
    return {"season": 2026, "is_home": True, **fields}


class TestTeamGameRecord:
    def test_regulation_win_from_the_home_side(self):
        game = _event("1", "2026-01-10", NYR, BOS, [1, 2, 0], [0, 1, 0])

        record = _record(game, NYR)

        assert (record["goals_for"], record["goals_against"], record["goal_diff"]) == (3, 1, 2)
        assert (record["won"], record["regulation_win"], record["standings_points"]) == (1, 1, 2)
        assert (record["went_to_overtime"], record["overtime_win"], record["one_goal_game"]) == (0, None, 0)
        assert (record["is_home"], record["opponent_id"], record["site_team_id"]) == (True, BOS, NYR)
        assert (record["goals_for_p1"], record["goals_for_p2"], record["goals_for_p3"]) == (1, 2, 0)
        assert record["goals_against_p2"] == 1

    def test_overtime_loss_earns_one_point(self):
        game = _event("1", "2026-01-10", NYR, BOS, [1, 1, 0, 1], [1, 1, 0, 0])

        loser = _record(game, BOS)

        assert (loser["won"], loser["regulation_win"], loser["standings_points"], loser["overtime_win"]) == (0, 0, 1, 0)
        assert (loser["is_home"], loser["site_team_id"]) == (False, NYR)
        assert _record(game, NYR)["standings_points"] == 2
        assert _record(game, NYR)["regulation_win"] == 0

    def test_shootout_goal_is_not_counted_as_a_goal(self):
        game = _event("1", "2026-01-10", NYR, BOS, [1, 1, 0, 0], [1, 1, 0, 0], shootout_winner="home")

        winner, loser = _record(game, NYR), _record(game, BOS)

        assert game["participants"][0]["result"]["score"] == 3  # ESPN's final score keeps the extra goal
        assert (winner["goals_for"], winner["goals_against"], winner["goal_diff"]) == (2, 2, 0)
        assert (winner["won"], winner["overtime_win"], winner["one_goal_game"]) == (1, 1, 1)
        assert (loser["won"], loser["standings_points"]) == (0, 1)

    def test_box_score_counts_for_and_against(self):
        game = _event("1", "2026-01-10", NYR, BOS, [1, 2, 0], [0, 1, 0])
        own = _team_box("1", NYR, shots_total=32, shots_missed=11, blocked_shots=14, saves=24, shots_against=25,
                        power_play_goals=1, power_play_opportunities=4, faceoffs_won=30)
        opponent = _team_box("1", BOS, shots_total=25, shots_missed=9, blocked_shots=10,
                             power_play_goals=1, power_play_opportunities=3, faceoffs_won=26)

        record = _record(game, NYR, own, opponent)

        assert (record["shots_for"], record["shots_against"]) == (32, 25)
        assert record["shot_attempts_for"] == 32 + 11 + 10      # own shots + own misses + opponent's blocks
        assert record["shot_attempts_against"] == 25 + 9 + 14
        assert (record["pp_goals"], record["pp_opportunities"]) == (1, 4)
        assert (record["times_shorthanded"], record["pp_goals_against"], record["kills"]) == (3, 1, 2)
        assert (record["faceoffs_won"], record["faceoffs_lost"]) == (30, 26)
        assert record["expected_goals_gap"] == pytest.approx(3 - 32 * nhl.LEAGUE_SHOOTING_PCT)

    def test_box_fields_are_none_without_a_box_score(self):
        record = _record(_event("1", "2026-01-10", NYR, BOS, [1, 2, 0], [0, 1, 0]), NYR)

        assert record["shots_for"] is None
        assert record["shot_attempts_for"] is None
        assert record["kills"] is None
        assert record["goals_for"] == 3

    def test_none_for_a_team_not_in_the_event(self):
        assert _record(_event("1", "2026-01-10", NYR, BOS, [1, 2, 0], [0, 1, 0]), LA) is None
        assert nhl.team_game_record({"participants": [{"entity_id": NYR}]}, NYR, None, None) is None


class TestRollingTeamFeatures:
    def test_windows_and_season_to_date(self):
        this_season = [_plain_record(goals_for=4, goals_against=2, goal_diff=2, won=1, standings_points=2) for _ in range(3)]
        last_season = [_plain_record(season=2025, goals_for=1, goals_against=3, goal_diff=-2, won=0, standings_points=0) for _ in range(12)]

        features = nhl.rolling_team_features(this_season + last_season, 2026)

        assert features["games_last10"] == 10
        assert features["goals_for_last10"] == pytest.approx((3 * 4 + 7 * 1) / 10)
        assert features["games_last25"] == 15
        assert (features["games_this_season"], features["goals_for_season"], features["points_pct_season"]) == (3, 4, 1.0)
        assert features["points_pct_last10"] == pytest.approx(3 * 2 / 20)
        assert features["win_streak"] == 3

    def test_shares_are_ratios_of_sums_not_means_of_ratios(self):
        records = [_plain_record(shots_for=40, shots_against=20), _plain_record(shots_for=10, shots_against=30)]

        features = nhl.rolling_team_features(records, 2026)

        assert features["shot_share_last10"] == pytest.approx(50 / 100)

    def test_percentages_are_shrunk_toward_the_league_mean(self):
        records = [_plain_record(goals_for=6, shots_for=30, saves=30, goalie_shots_against=30,
                                 pp_goals=2, pp_opportunities=2, kills=0, times_shorthanded=2)]

        features = nhl.rolling_team_features(records, 2026)

        shooting = (6 + nhl.LEAGUE_SHOOTING_PCT * nhl.TEAM_SHOT_PRIOR) / (30 + nhl.TEAM_SHOT_PRIOR)
        save = (30 + nhl.LEAGUE_SAVE_PCT * nhl.TEAM_SHOT_PRIOR) / (30 + nhl.TEAM_SHOT_PRIOR)
        power_play = (2 + nhl.LEAGUE_POWER_PLAY_PCT * nhl.SPECIAL_TEAMS_PRIOR) / (2 + nhl.SPECIAL_TEAMS_PRIOR)
        penalty_kill = (0 + (1 - nhl.LEAGUE_POWER_PLAY_PCT) * nhl.SPECIAL_TEAMS_PRIOR) / (2 + nhl.SPECIAL_TEAMS_PRIOR)
        assert features["shooting_pct_last10"] == pytest.approx(shooting)
        assert 0.095 < features["shooting_pct_last10"] < 0.2          # one hot night barely moves it
        assert features["save_pct_last10"] == pytest.approx(save)
        assert features["pdo_last10"] == pytest.approx(shooting + save)
        assert features["pp_pct_last10"] == pytest.approx(power_play)
        assert features["pk_pct_last10"] == pytest.approx(penalty_kill)
        assert features["net_special_teams_last10"] == pytest.approx(power_play + penalty_kill - 1)

    def test_road_only_version_of_the_arena_scored_counts(self):
        records = [_plain_record(hits=40, is_home=True), _plain_record(hits=20, is_home=False), _plain_record(hits=10, is_home=False)]

        features = nhl.rolling_team_features(records, 2026)

        assert features["hits_last25"] == pytest.approx(70 / 3)
        assert features["hits_road_last25"] == 15

    def test_recency_weighted_goals_lean_toward_the_latest_game(self):
        records = [_plain_record(goals_for=6, goals_against=0)] + [_plain_record(goals_for=2, goals_against=0) for _ in range(9)]

        features = nhl.rolling_team_features(records, 2026)

        assert features["goals_for_last10"] == pytest.approx(2.4)
        assert features["goals_for_ewm"] > features["goals_for_last10"]

    def test_overtime_win_pct_is_shrunk_hard_toward_a_coin_flip(self):
        records = [_plain_record(overtime_win=1), _plain_record(overtime_win=1), _plain_record(overtime_win=None)]

        features = nhl.rolling_team_features(records, 2026)

        assert features["overtime_win_pct"] == pytest.approx((2 + 0.5 * 10) / (2 + 10))

    def test_home_and_road_records_this_season(self):
        records = [
            _plain_record(is_home=True, won=1), _plain_record(is_home=True, won=0),
            _plain_record(is_home=False, won=1), _plain_record(season=2025, is_home=False, won=0),
        ]

        features = nhl.rolling_team_features(records, 2026)

        assert (features["home_record_pct"], features["road_record_pct"]) == (0.5, 1.0)

    def test_no_history_gives_none_not_zero_and_the_same_columns(self):
        empty = nhl.rolling_team_features([], 2026)
        full = nhl.rolling_team_features([_plain_record(goals_for=3)], 2026)

        assert set(empty) == set(full)
        assert empty["goals_for_last10"] is None
        assert empty["shooting_pct_last25"] is None
        assert empty["overtime_win_pct"] is None
        assert (empty["games_last10"], empty["win_streak"]) == (0, 0)

    def test_losing_streak_is_negative_and_stops_at_a_win(self):
        records = [_plain_record(won=0), _plain_record(won=0), _plain_record(won=1), _plain_record(won=0)]

        assert nhl.rolling_team_features(records, 2026)["win_streak"] == -2


class TestScheduleFeatures:
    def _history(self, *games):
        """games: (date, home_id, away_id), most recent first -> NYR's records."""
        return [_record(_event(str(i), day, home, away, [1, 1, 1], [0, 0, 0]), NYR) for i, (day, home, away) in enumerate(games)]

    def test_back_to_back_on_the_road_after_travel(self):
        history = self._history(("2026-01-09", NYR, BOS), ("2026-01-07", NYR, NYI), ("2026-01-04", LA, NYR))
        game = _event("9", "2026-01-10", VAN, NYR, [0, 0, 0], [0, 0, 0], kickoff_time="2026-01-11T03:00Z")

        features = nhl.schedule_features(history, game, NYR, False, VAN)

        assert (features["rest_days"], features["is_back_to_back"]) == (1, 1)
        assert (features["games_last_4_days"], features["games_last_7_days"]) == (2, 3)
        assert (features["road_trip_game_number"], features["home_stand_game_number"]) == (1, 0)
        assert 3800 < features["travel_km"] < 4000          # New York -> Vancouver
        assert features["timezone_shift_hours"] == -3
        assert features["local_start_hour"] == 22            # 10 PM on a New York body clock

    def test_third_straight_road_game(self):
        history = self._history(("2026-01-08", LA, NYR), ("2026-01-06", VAN, NYR), ("2026-01-03", NYR, BOS))
        game = _event("9", "2026-01-10", BOS, NYR, [0, 0, 0], [0, 0, 0])

        features = nhl.schedule_features(history, game, NYR, False, BOS)

        assert features["road_trip_game_number"] == 3
        assert features["rest_days"] == 2

    def test_home_stand_and_no_travel(self):
        history = self._history(("2026-01-08", NYR, BOS), ("2026-01-06", NYR, NYI), ("2026-01-03", LA, NYR))
        game = _event("9", "2026-01-10", NYR, VAN, [0, 0, 0], [0, 0, 0])

        features = nhl.schedule_features(history, game, NYR, True, NYR)

        assert (features["home_stand_game_number"], features["road_trip_game_number"]) == (3, 0)
        assert (features["travel_km"], features["timezone_shift_hours"]) == (0, 0)

    def test_first_game_after_the_off_season_has_no_travel_or_streak(self):
        history = self._history(("2025-04-15", LA, NYR),)
        game = _event("9", "2025-10-08", NYR, BOS, [0, 0, 0], [0, 0, 0])

        features = nhl.schedule_features(history, game, NYR, True, NYR)

        assert features["rest_days"] == 176
        assert (features["travel_km"], features["timezone_shift_hours"]) == (None, None)
        assert features["home_stand_game_number"] == 1
        assert features["games_last_7_days"] == 0

    def test_no_history(self):
        game = _event("9", "2026-01-10", NYR, BOS, [0, 0, 0], [0, 0, 0])

        features = nhl.schedule_features([], game, NYR, True, NYR)

        assert (features["rest_days"], features["is_back_to_back"], features["travel_km"]) == (None, None, None)
        assert features["home_stand_game_number"] == 1


class TestContextFeatures:
    def test_regular_season_divisional_game(self):
        game = _event("9", "2026-01-10", NYR, NYI, [0, 0, 0], [0, 0, 0])
        earlier = [_record(_event("1", "2025-12-01", NYI, NYR, [1, 0, 0], [2, 1, 1]), NYR)]

        context = nhl.context_features(game, NYR, NYI, earlier)

        assert (context["is_divisional_game"], context["is_conference_game"]) == (1, 1)
        assert (context["is_playoff"], context["series_game_number"], context["is_elimination_game"]) == (0, 0, 0)
        assert context["h2h_goal_diff_last_5"] == 3

    def test_playoff_series_state_from_earlier_games_in_the_series(self):
        def series_game(i, day, home, away, home_wins):
            periods = ([2, 1, 0], [0, 0, 0]) if home_wins else ([0, 0, 0], [2, 1, 0])
            return _event(str(i), day, home, away, *periods, season_type=3)

        # NYR lead 3-1 going into game 5.
        earlier = [
            _record(series_game(4, "2026-05-07", BOS, NYR, False), NYR),
            _record(series_game(3, "2026-05-05", BOS, NYR, True), NYR),
            _record(series_game(2, "2026-05-03", NYR, BOS, True), NYR),
            _record(series_game(1, "2026-05-01", NYR, BOS, True), NYR),
            _record(_event("0", "2026-01-01", NYR, BOS, [0, 0, 0], [3, 0, 0]), NYR),  # regular season -- ignored
        ]
        game = _event("5", "2026-05-09", NYR, BOS, [0, 0, 0], [0, 0, 0], season_type=3)

        context = nhl.context_features(game, NYR, BOS, earlier)

        assert context["is_playoff"] == 1
        assert (context["series_game_number"], context["series_lead"], context["is_elimination_game"]) == (5, 2, 1)

    def test_unknown_alignment_is_none(self):
        context = nhl.context_features(_event("9", "2026-01-10", NYR, "999", [0, 0, 0], [0, 0, 0]), NYR, "999", [])

        assert (context["is_divisional_game"], context["is_conference_game"]) == (None, None)
        assert context["h2h_goal_diff_last_5"] is None

    def test_team_injury_count_uses_the_hockey_statuses(self):
        injuries = [{"status": "Injured Reserve"}, {"status": "Out"}, {"status": "Day-To-Day"}, {"status": "Suspension"}]

        assert nhl.team_injury_count(injuries) == 3
        assert nhl.team_injury_count([]) == 0
        assert nhl.team_injury_count(None) is None


class TestLineupFeatures:
    def _lines(self, games=6):
        # Three regulars: a 20-minute star, and two 10-minute depth skaters.
        return [{"star": (1200, 2), "depth1": (600, 0), "depth2": (600, 1), f"callup{i}": (300, 0)} for i in range(games)]

    def test_missing_star_costs_half_the_regular_ice_time(self):
        features = nhl.lineup_features(self._lines(), {"depth1", "depth2"})

        assert features["ice_time_share_missing"] == pytest.approx(1200 / 2400)
        assert (features["regulars_missing"], features["top_scorers_out"]) == (1, 1)

    def test_full_lineup_and_one_off_call_ups_are_not_regulars(self):
        features = nhl.lineup_features(self._lines(), {"star", "depth1", "depth2"})

        assert features == {"ice_time_share_missing": 0, "regulars_missing": 0, "top_scorers_out": 0}

    def test_unknown_without_enough_history_or_without_tonights_lineup(self):
        unknown = {"ice_time_share_missing": None, "regulars_missing": None, "top_scorers_out": None}

        assert nhl.lineup_features(self._lines(games=4), {"star"}) == unknown
        assert nhl.lineup_features(self._lines(), None) == unknown

    def test_skater_game_line_keeps_only_skaters_who_played(self):
        players = [
            _skater("1", "2026-01-10", NYR, "s1", toi=900, points=2), _skater("1", "2026-01-10", NYR, "s2", toi=0),
            _goalie("1", "2026-01-10", NYR, "g1"),
        ]

        assert nhl.skater_game_line(players) == {"s1": (900, 2)}


class TestGoalieFeatures:
    def _start(self, day, saves=27, shots=30, toi=3600, started=True, season=2026):
        return nhl.goalie_game_record(_goalie("x", day, NYR, "g1", saves=saves, shots=shots, toi=toi, started=started), season)

    def test_goalie_game_record(self):
        record = self._start("2026-01-09", saves=19, shots=24, toi=1713)

        assert record["saves_above_average"] == pytest.approx(19 - nhl.LEAGUE_SAVE_PCT * 24)
        assert (record["quality_start"], record["pulled"]) == (0, 1)
        relief = self._start("2026-01-09", started=False)
        assert (relief["quality_start"], relief["pulled"]) == (None, None)

    def test_quality_and_rates_over_his_starts(self):
        history = [self._start("2026-01-08", saves=28, shots=30), self._start("2026-01-05", saves=24, shots=30)]

        features = nhl.goalie_features("g1", history, ["g1", "g2", "g1"], {"g1": history}, "2026-01-10", 2026)

        shrunk = (52 + nhl.LEAGUE_SAVE_PCT * nhl.GOALIE_SHOT_PRIOR) / (60 + nhl.GOALIE_SHOT_PRIOR)
        assert features["save_pct_last10"] == pytest.approx(shrunk)
        assert features["save_pct_season"] == pytest.approx(shrunk)
        assert features["save_pct_career"] == pytest.approx(shrunk)
        assert features["goals_against_per_60"] == pytest.approx(8 / 7200 * 3600)
        assert features["shots_faced_per_60"] == pytest.approx(30)
        assert features["saves_above_average_per_60"] == pytest.approx((52 - nhl.LEAGUE_SAVE_PCT * 60) / 2)
        assert features["quality_start_rate"] == 0.5
        assert features["pulled_rate"] == 0
        assert features["career_starts"] == 2

    def test_workload(self):
        history = [
            self._start("2026-01-09"), self._start("2026-01-07", started=False), self._start("2026-01-05"),
            self._start("2025-12-20"),
        ]

        features = nhl.goalie_features("g1", history, ["g1", "g2", "g1"], {"g1": history}, "2026-01-10", 2026)

        assert (features["rest_days"], features["started_yesterday"], features["days_since_last_start"]) == (1, 1, 1)
        assert (features["starts_last_7"], features["starts_last_14"]) == (2, 2)
        assert features["consecutive_starts"] == 1

    def test_long_layoff_is_capped_and_relief_appearances_do_not_count_as_starts(self):
        history = [self._start("2026-01-08", started=False), self._start("2025-11-01")]

        features = nhl.goalie_features("g1", history, [], {}, "2026-01-10", 2026)

        assert features["rest_days"] == 2
        assert features["days_since_last_start"] == nhl.MAX_DAYS_SINCE_LAST_START
        assert features["started_yesterday"] == 0
        assert features["career_starts"] == 1

    def test_tandem_share_backup_flag_and_save_gap(self):
        number_one = [self._start(f"2025-12-{d:02d}", saves=28, shots=30) for d in range(1, 9)]
        backup = [self._start(f"2025-12-{d:02d}", saves=25, shots=30) for d in (10, 12)]
        starters = ["g2", "g1", "g2"] + ["g1"] * 7
        team = {"g1": number_one, "g2": backup}

        starter_features = nhl.goalie_features("g1", number_one, starters, team, "2026-01-10", 2026)
        backup_features = nhl.goalie_features("g2", backup, starters, team, "2026-01-10", 2026)

        assert (starter_features["start_share"], starter_features["is_backup_start"]) == (0.8, 0)
        assert (backup_features["start_share"], backup_features["is_backup_start"]) == (0.2, 1)
        gap = nhl._goalie_save_pct(number_one) - nhl._goalie_save_pct(backup)
        assert starter_features["starter_vs_backup_save_gap"] == pytest.approx(gap)
        assert gap > 0
        assert backup_features["consecutive_starts"] == 1

    def test_debut_has_no_quality_numbers_and_the_same_columns(self):
        features = nhl.goalie_features("g9", [], ["g1"] * 6, {}, "2026-01-10", 2026)

        assert set(features) == set(nhl.empty_goalie_features())
        assert features["save_pct_career"] is None
        assert features["rest_days"] is None
        assert (features["career_starts"], features["start_share"], features["is_backup_start"]) == (0, 0, 1)

    def test_start_share_is_unknown_early_in_a_season(self):
        features = nhl.goalie_features("g1", [], ["g1", "g1"], {}, "2026-01-10", 2026)

        assert (features["start_share"], features["is_backup_start"]) == (None, None)


class TestBuildEventFeatures:
    def _game(self, **kwargs):
        return _event("9", "2026-01-10", NYR, BOS, [1, 1, 0, 0], [1, 1, 0, 0], shootout_winner="away", **kwargs)

    def test_ids_elo_labels(self):
        game = self._game()
        elo = {game["event_key"]: {"home_pre_rating": 1540.0, "away_pre_rating": 1500.0}}

        row = nhl.build_event_features(game, elo, [], [])

        assert (row["event_key"], row["home_entity_id"], row["away_entity_id"], row["season"]) == (game["event_key"], NYR, BOS, 2026)
        assert (row["home_elo"], row["away_elo"], row["elo_diff"]) == (1540.0, 1500.0, 40.0)
        assert row["label_home_won"] is False
        assert (row["label_home_score"], row["label_away_score"]) == (2, 3)     # ESPN's final, with the shootout goal
        assert (row["label_home_goals"], row["label_away_goals"]) == (2, 2)     # goals through overtime
        assert (row["label_went_to_overtime"], row["label_decided_by_shootout"]) == (True, True)

    def test_both_sides_get_the_same_feature_set_and_diffs(self):
        home_history = [_plain_record(goals_for=4, goals_against=2, goal_diff=2, event_date="2026-01-08", standings_points=2)]
        away_history = [_plain_record(goals_for=2, goals_against=3, goal_diff=-1, event_date="2026-01-07", standings_points=0)]

        row = nhl.build_event_features(self._game(), {}, home_history, away_history)

        home_columns = {name[len("home_"):] for name in row if name.startswith("home_") and name not in ("home_entity_id", "home_elo")}
        away_columns = {name[len("away_"):] for name in row if name.startswith("away_") and name not in ("away_entity_id", "away_elo")}
        assert home_columns == away_columns
        assert row["diff_goal_diff_last25"] == 3
        assert row["diff_points_pct_season"] == 1.0
        assert row["diff_rest_days"] == -1
        assert row["home_elo"] is None
        assert row["elo_diff"] is None

    def test_goalie_lineup_and_injury_columns(self):
        game = {**self._game(), "home_injuries": [{"status": "Out"}]}
        home_goalie = {**nhl.empty_goalie_features(), "save_pct_career": 0.915}
        home_lineup = {"ice_time_share_missing": 0.1, "regulars_missing": 2, "top_scorers_out": 1}

        row = nhl.build_event_features(game, {}, [], [], home_goalie=home_goalie, home_lineup=home_lineup)

        assert row["home_goalie_save_pct_career"] == 0.915
        assert row["away_goalie_save_pct_career"] is None
        assert (row["home_ice_time_share_missing"], row["home_top_scorers_out"]) == (0.1, 1)
        assert row["away_ice_time_share_missing"] is None
        assert (row["home_team_injury_count"], row["away_team_injury_count"]) == (1, None)

    def test_expected_power_play_goals_blends_both_teams(self):
        home_history = [_plain_record(pp_goals=1, pp_opportunities=4, kills=3, times_shorthanded=3)]
        away_history = [_plain_record(pp_goals=0, pp_opportunities=2, kills=1, times_shorthanded=2)]

        row = nhl.build_event_features(self._game(), {}, home_history, away_history)

        opportunities = (4 + 2) / 2
        conversion = (row["home_pp_pct_last25"] + (1 - row["away_pk_pct_last25"])) / 2
        assert row["home_expected_pp_goals"] == pytest.approx(opportunities * conversion)

    def test_every_value_is_a_number_bool_string_or_none(self):
        row = nhl.build_event_features(self._game(), {}, [], [])

        assert all(value is None or isinstance(value, (int, float, str, bool)) for value in row.values())


class TestBuildGoalieFeatures:
    def test_row_from_the_away_goalies_side(self):
        game = _event("9", "2026-01-10", NYR, BOS, [1, 2, 0], [0, 1, 0])
        elo = {game["event_key"]: {"home_pre_rating": 1540.0, "away_pre_rating": 1500.0}}
        starter = _goalie("9", "2026-01-10", BOS, "g1", saves=27, shots=30)
        goalie_features = {**nhl.empty_goalie_features(), "save_pct_career": 0.91}
        bruins = [_plain_record(shots_against=28, event_date="2026-01-09", is_home=False)]
        rangers = [_plain_record(shots_for=33, goals_for=3)]

        row = nhl.build_goalie_features(starter, game, elo, goalie_features, bruins, rangers)

        assert (row["entity_id"], row["team_id"], row["opponent_id"], row["is_home"]) == ("g1", BOS, NYR, 0)
        assert (row["own_elo"], row["opponent_elo"], row["elo_diff"]) == (1500.0, 1540.0, -40.0)
        assert row["save_pct_career"] == 0.91
        assert row["opponent_shots_for_last10"] == 33
        assert row["team_shots_against_last25"] == 28
        assert (row["team_rest_days"], row["team_is_back_to_back"]) == (1, 1)
        assert row["label_stat_line"] == starter["stat_line"]
        assert row["label_started"] is True


class TestFeatureGroups:
    def test_side_prefix_and_window_suffix_are_ignored(self):
        assert nhl.feature_group("home_shot_share_last25") == "shot_volume"
        assert nhl.feature_group("away_hits_road_last25") == "possession"
        assert nhl.feature_group("diff_points_pct_season") == "scoring_form"
        assert nhl.feature_group("home_goals_for_ewm") == "scoring_form"
        assert nhl.feature_group("away_games_this_season") == "scoring_form"
        assert nhl.feature_group("h2h_goal_diff_last_5") == "context"
        assert nhl.feature_group("elo_diff") == "elo"

    def test_goalie_columns_are_their_own_group(self):
        assert nhl.feature_group("home_goalie_save_pct_last25") == "goalie"
        assert nhl.feature_group("away_goalie_rest_days") == "goalie"
        assert nhl.feature_group("home_rest_days") == "schedule"

    def test_identifiers_and_labels_belong_to_no_group(self):
        assert nhl.feature_group("season") is None
        assert nhl.feature_group("home_entity_id") is None
        assert nhl.feature_group("label_home_won") is None

    def test_every_feature_column_of_a_real_row_is_in_exactly_one_group(self):
        game = _event("9", "2026-01-10", NYR, BOS, [1, 1, 0], [0, 1, 0])
        row = nhl.build_event_features(game, {}, [], [])

        ungrouped = [c for c in row if nhl.feature_group(c) is None]

        assert sorted(ungrouped) == sorted([
            "event_key", "event_date", "season", "home_entity_id", "away_entity_id", "label_home_won",
            "label_home_score", "label_away_score", "label_home_goals", "label_away_goals",
            "label_went_to_overtime", "label_decided_by_shootout",
        ])

    def test_event_feature_columns_lists_a_groups_columns(self):
        columns = nhl.event_feature_columns({"lineup", "goalie"})

        assert "home_ice_time_share_missing" in columns
        assert "away_goalie_save_pct_career" in columns
        assert "home_shot_share_last25" not in columns
        assert nhl.event_feature_columns(set()) == frozenset()


class TestSkaterFeatures:
    def _games(self, count, **stats):
        line = {"time_on_ice_seconds": 1200, "power_play_time_on_ice_seconds": 120, "short_handed_time_on_ice_seconds": 0,
                "shots_total": 3, "shots_missed": 1, "goals": 1, "assists": 1, "points": 2, "hits": 2, "blocked_shots": 1, **stats}
        return [{"entity_id": "s1", "stat_line": dict(line)} for _ in range(count)]

    def test_rates_are_per_60_minutes_of_his_own_ice_time(self):
        features = nhl.skater_features(self._games(4))

        assert features["toi_last25"] == 1200
        assert features["pp_toi_last25"] == 120
        assert features["shots_per_60"] == pytest.approx(9)
        assert features["shot_attempts_per_60"] == pytest.approx(12)
        assert features["points_per_60"] == pytest.approx(6)
        assert features["blocks_per_60"] == pytest.approx(3)

    def test_ice_time_trend_is_the_last_three_games_against_the_last_ten(self):
        promoted = self._games(3, time_on_ice_seconds=1500) + self._games(7, time_on_ice_seconds=1000)

        assert nhl.skater_features(promoted)["toi_trend"] == pytest.approx(1500 - (3 * 1500 + 7 * 1000) / 10)

    def test_shooting_percentage_is_shrunk_and_a_hot_streak_shows_as_goals_above_expected(self):
        features = nhl.skater_features(self._games(5, goals=2, shots_total=4))

        shrunk = (10 + nhl.LEAGUE_SHOOTING_PCT * nhl.SKATER_SHOT_PRIOR) / (20 + nhl.SKATER_SHOT_PRIOR)
        assert features["shooting_pct_long"] == pytest.approx(shrunk)
        assert features["goals_minus_expected_last25"] == pytest.approx(2 - 4 * shrunk)

    def test_no_history_gives_none_and_the_same_columns(self):
        empty = nhl.skater_features([])

        assert set(empty) == set(nhl.skater_features(self._games(2)))
        assert all(value is None for value in empty.values())

    def test_position_flags(self):
        assert nhl.position_flags("C") == {"is_center": 1, "is_wing": 0, "is_defense": 0}
        assert nhl.position_flags("LW") == {"is_center": 0, "is_wing": 1, "is_defense": 0}
        assert nhl.position_flags("D") == {"is_center": 0, "is_wing": 0, "is_defense": 1}
        assert nhl.position_flags(None) == {"is_center": None, "is_wing": None, "is_defense": None}

    def test_role_in_the_recent_lineup(self):
        lines = [{"star": (1400, 1), "s1": (900, 0), "depth": (500, 0)} for _ in range(4)] + [{"star": (1400, 1), "depth": (500, 0)}]

        assert nhl.role_features("s1", lines) == {"games_missed_last_10": 1, "toi_rank_on_team": 2}
        assert nhl.role_features("star", lines) == {"games_missed_last_10": 0, "toi_rank_on_team": 1}
        assert nhl.role_features("callup", lines) == {"games_missed_last_10": 5, "toi_rank_on_team": None}
        assert nhl.role_features("s1", []) == {"games_missed_last_10": None, "toi_rank_on_team": None}


class TestGoaliePropColumns:
    def test_average_and_count_of_each_prop_stat_over_his_recent_starts(self):
        history = [
            nhl.goalie_game_record(_goalie("x", "2026-01-08", NYR, "g1", saves=30, shots=32), 2026),
            nhl.goalie_game_record(_goalie("x", "2026-01-06", NYR, "g1", saves=20, shots=24, started=False), 2026),
            nhl.goalie_game_record(_goalie("x", "2026-01-04", NYR, "g1", saves=26, shots=30), 2026),
        ]

        features = nhl.goalie_features("g1", history, [], {}, "2026-01-10", 2026)

        # The relief appearance is not a start, so it is left out.
        assert (features["avg_saves"], features["games_with_saves"]) == (28, 2)
        assert (features["avg_goals_against"], features["avg_shots_against"]) == (3, 31)

    def test_a_debut_has_no_averages(self):
        features = nhl.goalie_features("g9", [], [], {}, "2026-01-10", 2026)

        assert (features["avg_saves"], features["games_with_saves"]) == (None, 0)
