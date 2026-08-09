"""
Unit tests for library.season -- season-window membership, including the
calendar-year wraparound both real sports (NFL, NCAAFB) exercise. No AWS
involved.
"""
from datetime import date

from library.season import current_month_day, is_in_season


class TestCurrentMonthDay:
    def test_formats_a_given_date_as_month_dash_day(self):
        assert current_month_day(date(2026, 8, 9)) == "08-09"

    def test_zero_pads_single_digit_month_and_day(self):
        assert current_month_day(date(2026, 1, 5)) == "01-05"

    def test_defaults_to_todays_utc_date_when_none_given(self):
        assert current_month_day() == date.today().strftime("%m-%d")


class TestIsInSeasonNonWrapping:
    def test_true_inside_the_range(self):
        assert is_in_season("06-15", "05-01", "07-01") is True

    def test_true_on_the_start_boundary(self):
        assert is_in_season("05-01", "05-01", "07-01") is True

    def test_true_on_the_end_boundary(self):
        assert is_in_season("07-01", "05-01", "07-01") is True

    def test_false_before_the_range(self):
        assert is_in_season("04-30", "05-01", "07-01") is False

    def test_false_after_the_range(self):
        assert is_in_season("07-02", "05-01", "07-01") is False


class TestIsInSeasonWrapping:
    def test_nfl_season_true_in_early_season(self):
        # NFL: Aug 1 -- Feb 28, crosses Jan 1.
        assert is_in_season("09-15", "08-01", "02-28") is True

    def test_nfl_season_true_after_new_year(self):
        assert is_in_season("01-20", "08-01", "02-28") is True

    def test_nfl_season_true_on_start_boundary(self):
        assert is_in_season("08-01", "08-01", "02-28") is True

    def test_nfl_season_true_on_end_boundary(self):
        assert is_in_season("02-28", "08-01", "02-28") is True

    def test_nfl_season_false_in_the_off_season(self):
        assert is_in_season("05-15", "08-01", "02-28") is False

    def test_ncaafb_season_true_in_early_season(self):
        # NCAAFB: Jul 1 -- Jan 31, crosses Jan 1.
        assert is_in_season("09-15", "07-01", "01-31") is True

    def test_ncaafb_season_true_on_end_boundary(self):
        assert is_in_season("01-31", "07-01", "01-31") is True

    def test_ncaafb_season_false_just_after_end_boundary(self):
        assert is_in_season("02-01", "07-01", "01-31") is False

    def test_ncaafb_season_false_in_the_off_season(self):
        assert is_in_season("04-01", "07-01", "01-31") is False
