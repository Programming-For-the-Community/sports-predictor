"""
Unit tests for library.http.rate_limiter.RateLimiter's minimum-gap pacing.
"""
from unittest.mock import patch

from library.http import rate_limiter
from library.http.rate_limiter import RateLimiter


class TestWait:
    def test_first_call_never_sleeps(self):
        with patch.object(rate_limiter.time, "monotonic", return_value=1000.0), \
             patch.object(rate_limiter.time, "sleep") as sleep:
            RateLimiter(0.5).wait()

        sleep.assert_not_called()

    def test_a_call_inside_the_interval_sleeps_for_the_remainder(self):
        limiter = RateLimiter(0.5)
        with patch.object(rate_limiter.time, "monotonic", side_effect=[1000.0, 1000.0, 1000.2, 1000.5]), \
             patch.object(rate_limiter.time, "sleep") as sleep:
            limiter.wait()
            limiter.wait()

        sleep.assert_called_once()
        assert abs(sleep.call_args.args[0] - 0.3) < 1e-9
