import pytest

from library.performance import field_events, scorecard
from library.performance.scorecard import AmountSample, ChanceSample


def _golfer(entity, finish, status="finished", score=-10, rounds=None):
    return {
        "entity_id": entity,
        "result": {"finish_position": finish, "status": status, "score_to_par": score, "rounds": rounds or []},
    }


def _pga_event(participants, event_id="9", date="2026-09-17", event_type="field"):
    return {
        "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_date": date, "event_type": event_type,
        "tournament_name": "Biltmore Championship Asheville", "participants": participants,
    }


def _row(model_key, value, generated_at="2026-09-17T11:00:00Z"):
    return {"model_key": model_key, "predicted_value": {"value": value}, "generated_at": generated_at}


class TestPeriod:
    def test_one_period_per_event_labelled_with_the_first_two_words_of_its_name(self):
        period = field_events.period_for(_pga_event([]))

        assert period.label == "Biltmore Championship"
        assert period.key == "2026-09-17-9"

    def test_events_sort_chronologically(self):
        early = field_events.period_for(_pga_event([], event_id="1", date="2026-09-03"))
        late = field_events.period_for(_pga_event([], event_id="2", date="2026-09-17"))

        assert early.key < late.key


class TestPgaSamples:
    def _samples(self, participants, rows):
        event = _pga_event(participants)
        return field_events.collect_samples("pga", [event], {event["event_key"]: rows}), event

    def test_top_10_is_graded_against_the_final_finish(self):
        samples, _ = self._samples(
            [_golfer("1", 3), _golfer("2", 40), _golfer("3", None, status="cut")],
            [_row("MODEL#top-10-probability#v5#GOLFER#1", 0.7), _row("MODEL#top-10-probability#v5#GOLFER#2", 0.1),
             _row("MODEL#top-10-probability#v5#GOLFER#3", 0.05)],
        )

        by_probability = {s.probability: s.happened for s in samples["top-10-probability"]}
        assert by_probability == {0.7: True, 0.1: False, 0.05: False}

    def test_top_5_uses_its_own_cutoff(self):
        samples, _ = self._samples([_golfer("1", 7)], [_row("MODEL#top-5-probability#v2#GOLFER#1", 0.4)])

        assert samples["top-5-probability"] == [ChanceSample(field_events.period_for(_pga_event([])), 0.4, False)]

    def test_final_score_is_graded_only_for_golfers_who_finished(self):
        samples, _ = self._samples(
            [_golfer("1", 3, score=-12), _golfer("2", None, status="cut", score=3)],
            [_row("MODEL#projected-score-to-par#v4#GOLFER#1", -9.0), _row("MODEL#projected-score-to-par#v4#GOLFER#2", -1.0)],
        )

        assert [(s.predicted, s.actual) for s in samples["projected-score-to-par"]] == [(-9.0, -12)]

    def test_each_round_is_graded_against_its_forecast_in_the_one_pre_event_snapshot(self):
        rounds = [{"round": 1, "score_to_par": -4}, {"round": 2, "score_to_par": -2}]
        samples, _ = self._samples(
            [_golfer("1", 3, rounds=rounds)],
            [
                _row("SNAPSHOT#final_pregame#MODEL#round-1#v3#GOLFER#1", -1.0),
                _row("SNAPSHOT#final_pregame#MODEL#round-2#v3#GOLFER#1", -0.5),
                _row("MODEL#round-2#v3#GOLFER#1", 9.9),  # a later live recompute -- must not be graded
            ],
        )

        assert [(s.predicted, s.actual) for s in samples["round-1"]] == [(-1.0, -4)]
        assert [(s.predicted, s.actual) for s in samples["round-2"]] == [(-0.5, -2)]

    def test_a_round_nobody_played_is_not_graded(self):
        samples, _ = self._samples([_golfer("1", 3, rounds=[{"round": 1, "score_to_par": -4}])], [_row("MODEL#round-4#v3#GOLFER#1", 0.0)])

        assert "round-4" not in samples

    def test_match_play_and_cup_events_are_skipped(self):
        event = _pga_event([_golfer("1", 1)], event_type="match_play")

        assert field_events.collect_samples("pga", [event], {event["event_key"]: [_row("MODEL#top-10-probability#v5#GOLFER#1", 0.5)]}) == {}

    def test_an_event_with_no_predictions_is_skipped(self):
        event = _pga_event([_golfer("1", 1)])

        assert field_events.collect_samples("pga", [event], {event["event_key"]: []}) == {}


class TestF1Samples:
    def _race(self, drivers, event_type="field"):
        return {
            "event_key": "SPORT#F1#EVENT#5", "event_id": "5", "event_date": "2026-09-13", "event_type": event_type,
            "race_name": "Dutch Grand Prix", "participants": drivers,
        }

    def _driver(self, entity, finish, status="classified", grid=3, qualifying=None):
        return {"entity_id": entity, "result": {
            "finish_position": finish, "status": status, "grid_position": grid,
            "qualifying": {"position": qualifying} if qualifying else None,
        }}

    def test_win_podium_and_dnf_are_graded_against_the_race_result(self):
        race = self._race([self._driver("1", 1), self._driver("2", 8), self._driver("3", None, status="dnf")])
        rows = [
            _row(f"MODEL#{model}#v1#DRIVER#{d}", p)
            for model in ("win-probability", "podium-probability", "dnf-probability")
            for d, p in (("1", 0.6), ("2", 0.1), ("3", 0.2))
        ]

        samples = field_events.collect_samples("f1", [race], {race["event_key"]: rows})

        assert {s.probability: s.happened for s in samples["win-probability"]} == {0.6: True, 0.1: False, 0.2: False}
        assert {s.probability: s.happened for s in samples["podium-probability"]} == {0.6: True, 0.1: False, 0.2: False}
        assert {s.probability: s.happened for s in samples["dnf-probability"]} == {0.6: False, 0.1: False, 0.2: True}

    def test_finish_and_qualifying_positions_are_graded_as_amounts(self):
        race = self._race([self._driver("1", 2, qualifying=1), self._driver("2", None, status="dnf", qualifying=4)])
        rows = [
            _row("MODEL#projected-finish-position#v1#DRIVER#1", 3.4), _row("MODEL#projected-finish-position#v1#DRIVER#2", 5.0),
            _row("MODEL#projected-qualifying-position#v1#DRIVER#1", 2.0), _row("MODEL#projected-qualifying-position#v1#DRIVER#2", 6.0),
        ]

        samples = field_events.collect_samples("f1", [race], {race["event_key"]: rows})

        # the retired driver has no classified finish, so is not graded on finishing position
        assert [(s.predicted, s.actual) for s in samples["projected-finish-position"]] == [(3.4, 2)]
        assert sorted((s.predicted, s.actual) for s in samples["projected-qualifying-position"]) == [(2.0, 1), (6.0, 4)]

    def test_a_constructor_wins_when_its_driver_wins_the_race(self):
        race = self._race([
            {**self._driver("1", 1), "constructor_entity_id": "red_bull"},
            {**self._driver("2", 2), "constructor_entity_id": "red_bull"},
            {**self._driver("3", 3), "constructor_entity_id": "mclaren"},
        ])
        rows = [_row("MODEL#constructor-win-probability#v1#CONSTRUCTOR#red_bull", 0.6), _row("MODEL#constructor-win-probability#v1#CONSTRUCTOR#mclaren", 0.3)]

        samples = field_events.collect_samples("f1", [race], {race["event_key"]: rows})

        assert {s.probability: s.happened for s in samples["constructor-win-probability"]} == {0.6: True, 0.3: False}

    def test_a_constructor_race_with_no_winner_yet_is_not_graded(self):
        race = self._race([{**self._driver("1", None, status=None), "constructor_entity_id": "red_bull"}])

        samples = field_events.collect_samples("f1", [race], {race["event_key"]: [_row("MODEL#constructor-win-probability#v1#CONSTRUCTOR#red_bull", 0.6)]})

        assert samples == {}

    def test_a_sprint_uses_the_sprint_models(self):
        race = self._race([self._driver("1", 1, grid=2)], event_type="sprint")
        rows = [_row("MODEL#sprint-win-probability#v1#DRIVER#1", 0.5), _row("MODEL#projected-sprint-grid-position#v1#DRIVER#1", 3.0)]

        samples = field_events.collect_samples("f1", [race], {race["event_key"]: rows})

        assert samples["sprint-win-probability"][0].happened is True
        assert [(s.predicted, s.actual) for s in samples["projected-sprint-grid-position"]] == [(3.0, 2)]


class TestBuildRecords:
    def _cards(self):
        return [
            {"model_name": "top-10-probability", "version": 5, "accuracy": 0.9},
            {"model_name": "projected-score-to-par", "version": 4, "mae": 2.5},
            {"model_name": "projected-cut-line", "version": 1, "mae": 1.0},
        ]

    def test_reports_the_graded_models_with_the_sports_own_noun_and_skips_the_rest(self):
        records = {r["model_name"]: r for r in field_events.build_records("pga", {}, self._cards())}

        assert set(records) == {"top-10-probability", "projected-score-to-par"}
        assert records["top-10-probability"]["kind"] == "chance"
        assert records["top-10-probability"]["count_noun"] == "golfers"
        assert records["projected-score-to-par"]["kind"] == "amount"
        assert records["projected-score-to-par"]["margin_of_error"] == 2.5

    def test_a_constructor_model_counts_constructors_not_drivers(self):
        cards = [{"model_name": "constructor-win-probability", "version": 1, "accuracy": 0.9}, {"model_name": "podium-probability", "version": 1, "accuracy": 0.8}]

        records = {r["model_name"]: r for r in field_events.build_records("f1", {}, cards)}

        assert records["constructor-win-probability"]["count_noun"] == "constructors"
        assert records["podium-probability"]["count_noun"] == "drivers"

    def test_the_scorecard_is_marked_per_event(self):
        document = field_events.build_field_scorecard("pga", 2026, [], {}, [])

        assert document["period_kind"] == "event"


class TestChanceRecord:
    def _period(self, n):
        return scorecard.Period(f"2026-{n:02d}", f"E{n}")

    def test_accuracy_bands_by_stated_chance_and_share_called_right(self):
        p = self._period(1)
        # 5 golfers given ~10% (all correctly not top 10 except 1), 5 given ~70% (4 did)
        samples = [ChanceSample(p, 0.1, False)] * 4 + [ChanceSample(p, 0.1, True)] + [ChanceSample(p, 0.7, True)] * 4 + [ChanceSample(p, 0.7, False)]

        record = scorecard.chance_record("top-10-probability", 5, samples, 0.9, "golfers")

        assert record["kind"] == "chance"
        assert record["band_kind"] == "predicted_chance"
        assert record["season"]["value"] == pytest.approx(0.8)
        low, _, _, high, _ = record["bands"]
        assert (low["tag"], low["lo"], low["hi"], low["n"], low["pct"]) == ("LOW", 0.0, 0.2, 5, pytest.approx(0.8))
        assert (high["tag"], high["lo"], high["hi"], high["n"], high["pct"]) == ("HIGH", 0.6, 0.8, 5, pytest.approx(0.8))

    def test_the_top_of_the_range_is_included_in_the_last_band(self):
        record = scorecard.chance_record("m", 1, [ChanceSample(self._period(1), 1.0, True)] * 5, None)

        assert record["bands"][-1]["n"] == 5

    def test_the_baseline_is_always_answering_no(self):
        p = self._period(1)
        samples = [ChanceSample(p, 0.05, False)] * 9 + [ChanceSample(p, 0.6, True)]  # 100% right; always-no is right 90%

        record = scorecard.chance_record("m", 1, samples, None)

        assert record["vs_baseline_pct"] == pytest.approx((1.0 - 0.9) / 0.9 * 100)
