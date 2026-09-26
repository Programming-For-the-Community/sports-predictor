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

    def test_the_week_still_being_played_is_not_last_week_but_counts_toward_the_season(self):
        # Real bug (2026-09-26): NCAAFB week 4's Thursday/Friday games made
        # week 4 "last week" while its Saturday slate was still to play.
        samples = [_pick(WK2, True), _pick(WK2, False), _pick(WK3, True)]

        record = scorecard.pick_record("win-probability", 9, samples, None, open_period=WK3)

        assert record["last_period"] == {"label": "Wk 2", "value": 0.5, "n": 2}
        assert [p["label"] for p in record["periods"]] == ["Wk 2"]
        assert record["season"]["n"] == 3

    def test_an_open_period_leaves_amount_records_week_chips_too(self):
        record = scorecard.amount_record(
            "score-margin", 6, [_amount(WK1, 5, 3), _amount(WK2, 5, 1)], 10.0, 10.0, open_period=WK2,
        )

        assert record["last_period"]["label"] == "Wk 1"
        assert record["season"]["n"] == 2

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


class TestBest:
    def test_ranks_teams_by_how_often_their_games_were_picked_right(self):
        samples = [
            PickSample(WK1, True, 0.2, True, ("uga", "bama")),
            PickSample(WK2, True, 0.2, True, ("uga", "lsu")),
            PickSample(WK3, True, 0.2, True, ("uga", "tex")),
            PickSample(WK1, False, 0.2, True, ("osu", "mich")),
            PickSample(WK2, True, 0.2, True, ("osu", "psu")),
            PickSample(WK3, True, 0.2, True, ("osu", "ore")),
        ]

        best = scorecard.pick_record("win-probability", 9, samples, None, entity_type="team")["best"]

        assert best["entity_type"] == "team"
        assert [(e["entity_id"], e["n"]) for e in best["entities"]] == [("uga", 3), ("osu", 3)]
        assert best["entities"][1]["value"] == pytest.approx(2 / 3)

    def test_the_three_game_floor_applies_once_anyone_has_three(self):
        samples = [AmountSample(WK1, 10, 10, ("one-game",))] + [AmountSample(p, 10, 12, ("three-games",)) for p in (WK1, WK2, WK3)]

        best = scorecard.amount_record("score-margin", 6, samples, 5.0, 5.0, entity_type="team")["best"]

        assert [e["entity_id"] for e in best["entities"]] == ["three-games"]

    def test_before_anyone_has_three_every_entity_counts(self):
        samples = [AmountSample(WK1, 10, 11, ("a",)), AmountSample(WK1, 10, 14, ("b",))]

        best = scorecard.amount_record("score-margin", 6, samples, 5.0, 5.0, entity_type="team")["best"]

        assert [(e["entity_id"], e["value"]) for e in best["entities"]] == [("a", 1.0), ("b", 4.0)]

    def test_amounts_rank_by_smallest_miss_and_keep_the_top_five(self):
        samples = [AmountSample(WK1, 10, 10 + miss, (f"t{miss}",)) for miss in (6, 1, 5, 2, 4, 3)]

        best = scorecard.amount_record("score-margin", 6, samples, 5.0, 5.0, entity_type="team")["best"]

        assert [e["entity_id"] for e in best["entities"]] == ["t1", "t2", "t3", "t4", "t5"]

    def test_player_props_also_rank_by_miss_as_a_share_of_actual_yards(self):
        # The backup's 2-yard miss is the smallest raw miss but a third of his yards.
        samples = [AmountSample(WK1, 4, 6, ("backup",)), AmountSample(WK1, 90, 100, ("starter",))]

        record = scorecard.amount_record("player-prop-rushing-yards", 4, samples, 20.0, 20.0, entity_type="player", relative_best=True)

        assert [e["entity_id"] for e in record["best"]["entities"]] == ["backup", "starter"]
        assert [e["entity_id"] for e in record["best_relative"]["entities"]] == ["starter", "backup"]
        assert record["best_relative"]["entities"][0]["value"] == pytest.approx(0.1)

    def test_a_player_with_no_actual_yards_has_no_share_to_rank(self):
        record = scorecard.amount_record(
            "player-prop-rushing-yards", 4, [AmountSample(WK1, 5, 0, ("dnp",))], 20.0, 20.0, entity_type="player", relative_best=True,
        )

        assert record["best_relative"]["entities"] == []

    def test_no_best_when_no_prediction_names_a_team_or_player(self):
        record = scorecard.amount_record("score-margin", 1, [AmountSample(WK1, 5, 7)], 2.0, 2.0, entity_type="team")

        assert "best" not in record

    def test_no_best_without_an_entity_type(self):
        assert "best" not in scorecard.pick_record("win-probability", 9, [_pick(WK1, True)], None)
