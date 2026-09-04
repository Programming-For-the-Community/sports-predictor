"""
Unit tests for library.parsing's us_eastern_date/us_eastern_date_from_iso
-- the UTC-to-Eastern calendar-date conversion every sport's normalizer
uses for event_date, instead of truncating a raw UTC timestamp (which is
off by one day for any event starting at/after 8pm Eastern).
"""
from datetime import datetime, timezone

from library.parsing import us_eastern_date, us_eastern_date_from_iso


class TestUsEasternDate:
    def test_daytime_utc_matches_utc_date(self):
        # Midday UTC is well inside the same Eastern calendar date.
        assert us_eastern_date(datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)) == "2026-09-03"

    def test_early_utc_morning_is_still_the_previous_eastern_date(self):
        # 01:00 UTC is 9pm Eastern the day before -- a game that kicked
        # off in Eastern-evening prime time is still on the previous day's
        # calendar date once the UTC clock has crossed midnight.
        assert us_eastern_date(datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)) == "2026-09-03"

    def test_late_utc_evening_matches_utc_date(self):
        # 23:00 UTC is 7pm Eastern the same day.
        assert us_eastern_date(datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)) == "2026-09-03"


class TestUsEasternDateFromIso:
    def test_parses_zulu_suffix(self):
        assert us_eastern_date_from_iso("2026-09-04T00:00Z") == "2026-09-03"

    def test_parses_explicit_utc_offset(self):
        assert us_eastern_date_from_iso("2026-09-04T00:00+00:00") == "2026-09-03"

    def test_matches_direct_datetime_conversion(self):
        moment = datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)
        assert us_eastern_date_from_iso("2026-09-03T23:00Z") == us_eastern_date(moment)
