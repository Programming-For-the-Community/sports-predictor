import pytest

from library.performance import head_to_head
from library.performance.scorecard import AmountSample, PickSample


def _event(event_id, home_score, away_score, week=3, event_date="2026-09-27", season_type=2):
    return {
        "event_key": f"SPORT#NFL#EVENT#{event_id}", "event_date": event_date, "week": week, "season_type": season_type,
        "participants": [
            {"entity_id": "h", "role": "home", "result": {"score": home_score}},
            {"entity_id": "a", "role": "away", "result": {"score": away_score}},
        ],
    }


def _row(model_key, value, generated_at="2026-09-27T16:46:00Z"):
    return {"model_key": model_key, "predicted_value": value, "generated_at": generated_at}


def _rows(win_probability=0.7, margin=5.0, home=24.0, away=19.0):
    return [
        _row("MODEL#win-probability#v9", {"home_win_probability": win_probability}),
        _row("MODEL#score-margin#v6", {"value": margin}),
        _row("MODEL#home-score#v7", {"value": home}),
        _row("MODEL#away-score#v7", {"value": away}),
    ]


class TestPeriodFor:
    def test_football_regular_season_is_a_numbered_week(self):
        period = head_to_head.period_for("nfl", _event("1", 1, 0, week=3))

        assert (period.key, period.label) == ("1-03", "Wk 3")

    def test_football_postseason_is_one_playoffs_period_ordered_after_the_regular_season(self):
        regular = head_to_head.period_for("nfl", _event("1", 1, 0, week=18))
        playoff = head_to_head.period_for("nfl", _event("2", 1, 0, week=2, season_type=3))

        assert playoff.label == "Playoffs"
        assert playoff.key > regular.key

    def test_football_playoff_weeks_all_share_one_period(self):
        wild_card = head_to_head.period_for("nfl", _event("1", 1, 0, week=1, season_type=3))
        super_bowl = head_to_head.period_for("nfl", _event("2", 1, 0, week=5, season_type=3))

        assert wild_card == super_bowl

    def test_ncaafb_cfbd_regular_season_type_is_a_numbered_week(self):
        # Real bug (2026-09-26): NCAAFB stores CFBD's "regular"/"postseason"
        # strings, so every regular-season week was labeled "Playoffs".
        period = head_to_head.period_for("ncaafb", _event("1", 1, 0, week=4, season_type="regular"))

        assert period.label == "Wk 4"

    def test_ncaafb_cfbd_postseason_is_playoffs_after_the_regular_season(self):
        regular = head_to_head.period_for("ncaafb", _event("1", 1, 0, week=15, season_type="regular"))
        bowl = head_to_head.period_for("ncaafb", _event("2", 1, 0, week=1, season_type="postseason"))

        assert bowl.label == "Playoffs"
        assert bowl.key > regular.key

    def test_basketball_groups_by_the_monday_to_sunday_week(self):
        tuesday = head_to_head.period_for("nba", {"event_date": "2026-09-22"})  # a Tuesday
        sunday = head_to_head.period_for("nba", {"event_date": "2026-09-27"})
        next_monday = head_to_head.period_for("nba", {"event_date": "2026-09-28"})

        assert tuesday == sunday
        assert tuesday.label == "Sep 21"
        assert next_monday != sunday


class TestCollectSamples:
    def test_grades_the_pick_and_each_amount_against_the_final_result(self):
        events = [_event("1", 27, 20)]  # home won by 7

        samples = head_to_head.collect_samples("nfl", events, {events[0]["event_key"]: _rows()}, {})

        (pick,) = samples["win-probability"]
        assert isinstance(pick, PickSample)
        assert pick.correct is True
        assert pick.edge == pytest.approx(0.2)
        assert pick.baseline_correct is True
        assert samples["score-margin"] == [AmountSample(pick.period, 5.0, 7)]
        assert samples["home-score"][0].actual == 27
        assert samples["away-score"][0].actual == 20

    def test_an_underdog_pick_that_hit_and_the_home_baseline_that_missed(self):
        events = [_event("1", 17, 24)]  # away won
        rows = _rows(win_probability=0.3)  # model picked away

        (pick,) = head_to_head.collect_samples("nfl", events, {events[0]["event_key"]: rows}, {})["win-probability"]

        assert pick.correct is True
        assert pick.baseline_correct is False

    def test_events_without_a_prediction_or_a_result_are_skipped(self):
        no_prediction = _event("1", 27, 20)
        no_result = {**_event("2", 0, 0), "participants": [{"entity_id": "h", "role": "home"}, {"entity_id": "a", "role": "away"}]}

        samples = head_to_head.collect_samples("nfl", [no_prediction, no_result], {no_result["event_key"]: _rows()}, {})

        assert samples == {}

    def test_uses_the_most_recent_row_when_a_model_was_scored_more_than_once(self):
        events = [_event("1", 27, 20)]
        rows = [
            _row("MODEL#win-probability#v8", {"home_win_probability": 0.2}, "2026-09-20T00:00:00Z"),
            _row("MODEL#win-probability#v9", {"home_win_probability": 0.7}, "2026-09-27T16:46:00Z"),
        ]

        (pick,) = head_to_head.collect_samples("nfl", events, {events[0]["event_key"]: rows}, {})["win-probability"]

        assert pick.correct is True

    def test_player_props_are_graded_against_each_players_stat_line(self):
        events = [_event("1", 27, 20)]
        rows = _rows() + [
            _row("MODEL#player-prop-passing-yards#v4#PLAYER#p1", {"value": 280.0}),
            _row("MODEL#player-prop-passing-touchdowns#v3#PLAYER#p1", {"value": 2.0}),
            _row("MODEL#player-prop-passing-yards#v4#PLAYER#p2", {"value": 200.0}),  # never played
        ]
        stats = {events[0]["event_key"]: {"p1": {"passing_yards": 301}}}

        samples = head_to_head.collect_samples("nfl", events, {events[0]["event_key"]: rows}, stats)

        assert [(s.predicted, s.actual) for s in samples["player-prop-passing-yards"]] == [(280.0, 301)]
        # played but recorded no passing touchdowns -> 0, not skipped
        assert [(s.predicted, s.actual) for s in samples["player-prop-passing-touchdowns"]] == [(2.0, 0)]


class TestBuildRecords:
    def _cards(self):
        return [
            {"model_name": "win-probability", "version": 9, "accuracy": 0.66},
            {"model_name": "score-margin", "version": 6, "mae": 10.8},
            {"model_name": "player-prop-sacks", "version": 3, "mae": 0.9},
        ]

    def test_one_record_per_promoted_model_with_training_figures(self):
        events = [_event("1", 27, 20)]
        samples = head_to_head.collect_samples("nfl", events, {events[0]["event_key"]: _rows()}, {})

        records = {r["model_name"]: r for r in head_to_head.build_records(samples, self._cards())}

        assert set(records) == {"win-probability", "score-margin", "player-prop-sacks"}
        assert records["win-probability"]["kind"] == "pick"
        assert records["win-probability"]["at_training"] == 0.66
        assert records["score-margin"]["kind"] == "amount"
        assert records["score-margin"]["margin_of_error"] == 10.8
        assert records["score-margin"]["at_training"] == 10.8

    def test_a_promoted_model_with_no_graded_predictions_still_gets_an_empty_record(self):
        records = {r["model_name"]: r for r in head_to_head.build_records({}, self._cards())}

        assert records["player-prop-sacks"]["season"] == {"value": None, "n": 0}
        assert records["player-prop-sacks"]["bands"] == []

    def test_a_model_that_is_no_longer_promoted_is_not_reported(self):
        events = [_event("1", 27, 20)]
        samples = head_to_head.collect_samples("nfl", events, {events[0]["event_key"]: _rows()}, {})

        names = {r["model_name"] for r in head_to_head.build_records(samples, [self._cards()[0]])}

        assert names == {"win-probability"}


def test_build_head_to_head_scorecard_marks_the_period_kind_as_week():
    document = head_to_head.build_head_to_head_scorecard("nfl", 2026, [], {}, {}, [])

    assert document["period_kind"] == "week"
    assert document["models"] == []
