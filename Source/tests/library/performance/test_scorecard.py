import pytest

from library.performance import scorecard
from library.performance.scorecard import AmountSample, Period, PickSample

WK1, WK2, WK3 = Period("2026-01", "Wk 1"), Period("2026-02", "Wk 2"), Period("2026-03", "Wk 3")


def _pick(period, correct, edge=0.2, baseline_correct=True):
    return PickSample(period, correct, edge, baseline_correct)


class TestPickRecord:
    def test_season_and_last_period_accuracy(self):
        samples = [_pick(WK1, True), _pick(WK1, False), _pick(WK2, True), _pick(WK2, True), _pick(WK3, True), _pick(WK3, False)]

        record = scorecard.pick_record("win-probability", 9, samples, at_training=0.66)

        assert record["kind"] == "pick"
        assert record["season"] == {"value": pytest.approx(4 / 6), "n": 6}
        assert record["last_period"] == {"label": "Wk 3", "value": 0.5, "n": 2}
        assert [p["value"] for p in record["periods"]] == [0.5, 1.0, 0.5]
        assert record["at_training"] == 0.66

    def test_vs_baseline_is_relative_lift_over_always_picking_home(self):
        # 4 of 5 right vs "always home" right 3 of 5 -> +33% better
        samples = [_pick(WK1, True, baseline_correct=b) for b in (True, True, True, False, False)]
        samples[4] = _pick(WK1, False, baseline_correct=False)

        record = scorecard.pick_record("win-probability", 9, samples, None)

        assert record["vs_baseline_pct"] == pytest.approx((0.8 - 0.6) / 0.6 * 100)

    def test_bands_use_the_game_card_confidence_tiers(self):
        samples = (
            [_pick(WK1, True, edge=0.20)] * 5          # HIGH (>= 0.13)
            + [_pick(WK1, True, edge=0.08)] * 3 + [_pick(WK1, False, edge=0.08)] * 2  # MED
            + [_pick(WK1, False, edge=0.02)] * 5       # LOW
        )

        record = scorecard.pick_record("win-probability", 9, samples, None)

        high, med, low = record["bands"]
        assert (high["tag"], high["n"], high["pct"]) == ("HIGH", 5, 1.0)
        assert (med["tag"], med["n"], med["pct"]) == ("MED", 5, 0.6)
        assert (low["tag"], low["n"], low["pct"]) == ("LOW", 5, 0.0)

    def test_a_band_under_the_minimum_sample_is_early_with_no_percentage(self):
        record = scorecard.pick_record("win-probability", 9, [_pick(WK1, True, edge=0.2)] * 4, None)

        high = record["bands"][0]
        assert high["early"] is True
        assert high["pct"] is None
        assert high["n"] == 4

    def test_no_samples_yields_an_empty_record_rather_than_an_error(self):
        record = scorecard.pick_record("win-probability", 9, [], None)

        assert record["season"] == {"value": None, "n": 0}
        assert record["last_period"] is None
        assert record["vs_baseline_pct"] is None
        assert all(band["n"] == 0 and band["early"] for band in record["bands"])

    def test_periods_are_chronological_and_capped_to_the_most_recent_six(self):
        periods = [Period(f"2026-{i:02d}", f"Wk {i}") for i in range(1, 9)]

        record = scorecard.pick_record("win-probability", 9, [_pick(p, True) for p in reversed(periods)], None)

        assert [p["label"] for p in record["periods"]] == [f"Wk {i}" for i in range(3, 9)]


def _amount(period, predicted, actual):
    return AmountSample(period, predicted, actual)


class TestAmountRecord:
    def test_average_miss_for_the_season_and_last_period(self):
        samples = [_amount(WK1, 20, 25), _amount(WK1, 20, 15), _amount(WK2, 30, 30), _amount(WK2, 10, 16)]

        record = scorecard.amount_record("score-margin", 6, samples, at_training=10.8, margin_of_error=10.8)

        assert record["kind"] == "amount"
        assert record["season"] == {"value": pytest.approx((5 + 5 + 0 + 6) / 4), "n": 4}
        assert record["last_period"] == {"label": "Wk 2", "value": 3.0, "n": 2}

    def test_vs_baseline_compares_against_predicting_the_seasons_average(self):
        samples = [_amount(WK1, 10, 10), _amount(WK1, 20, 20), _amount(WK1, 30, 30)]  # perfect model

        record = scorecard.amount_record("score-margin", 6, samples, None, 5.0)

        assert record["vs_baseline_pct"] == pytest.approx(100.0)

    def test_bands_are_equal_thirds_of_the_predicted_range(self):
        samples = [_amount(WK1, p, p) for p in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)]

        record = scorecard.amount_record("home-score", 7, samples, None, 1.0)

        low, med, high = record["bands"]
        assert [b["tag"] for b in (low, med, high)] == ["LOW", "MED", "HIGH"]
        assert (low["lo"], low["hi"]) == (0, 4)
        assert (med["lo"], med["hi"]) == (4, 8)
        assert (high["lo"], high["hi"]) == (8, 12)
        # the top of the range belongs to HIGH; nothing is dropped or double counted
        assert low["n"] + med["n"] + high["n"] == 13

    def test_band_percentage_is_the_share_within_the_margin_of_error(self):
        # 10 LOW predictions (all ~1), 6 of them within +/-2 of the actual
        low = [_amount(WK1, 1, 1 + miss) for miss in (0, 1, 2, -1, -2, 0, 5, -6, 7, 8)]
        high = [_amount(WK1, 30, 30)]
        record = scorecard.amount_record("passing-yards", 4, low + high, None, margin_of_error=2.0)

        assert record["bands"][0]["n"] == 10
        assert record["bands"][0]["pct"] == pytest.approx(0.6)
        assert record["margin_of_error"] == 2.0

    def test_missing_margin_of_error_falls_back_to_the_seasons_average_miss(self):
        samples = [_amount(WK1, 10, 12), _amount(WK1, 20, 24)]

        record = scorecard.amount_record("home-score", 7, samples, None, None)

        assert record["margin_of_error"] == pytest.approx(3.0)

    def test_no_bands_when_every_prediction_is_identical_or_there_are_none(self):
        assert scorecard.amount_record("m", 1, [], None, 1.0)["bands"] == []
        assert scorecard.amount_record("m", 1, [_amount(WK1, 5, 6)] * 3, None, 1.0)["bands"] == []


class TestBias:
    def test_a_band_that_over_predicted_leans_high_and_one_that_under_predicted_leans_low(self):
        # LOW band predictions run 2 above what happened; HIGH band runs 3 below.
        low = [_amount(WK1, 1, -1)] * 5
        high = [_amount(WK1, 30, 33)] * 5

        record = scorecard.amount_record("passing-yards", 4, low + high, None, 10.0)

        low_band, _, high_band = record["bands"]
        assert low_band["bias"] == pytest.approx(2.0)
        assert high_band["bias"] == pytest.approx(-3.0)

    def test_season_bias_is_the_average_signed_error(self):
        samples = [_amount(WK1, 10, 8), _amount(WK1, 10, 12), _amount(WK2, 20, 14)]

        assert scorecard.amount_record("m", 1, samples, None, 5.0)["bias"] == pytest.approx((2 - 2 + 6) / 3)

    def test_an_early_band_reports_no_bias(self):
        samples = [_amount(WK1, 0, 1)] * 2 + [_amount(WK1, 30, 33)] * 6

        low_band = scorecard.amount_record("m", 1, samples, None, 5.0)["bands"][0]

        assert low_band["early"] is True
        assert low_band["bias"] is None

    def test_picks_have_no_bias_field_on_the_record(self):
        assert "bias" not in scorecard.pick_record("win-probability", 9, [], None)


def test_build_scorecard_wraps_records_with_the_period_kind():
    document = scorecard.build_scorecard("nfl", 2026, "week", [{"model_name": "win-probability"}])

    assert document["sport"] == "nfl"
    assert document["season"] == 2026
    assert document["period_kind"] == "week"
    assert document["models"] == [{"model_name": "win-probability"}]
    assert document["generated_at"]
