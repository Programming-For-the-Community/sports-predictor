"""
Unit tests for library.storage.ncaafb_coach_cache -- shared by
aws-lambdas/ncaafb/ingest/enrichment.py and data-backfills/ncaafb/
backfill.py (see this module's own docstring for why it has to live in
the shared library package, not a sibling Lambda's local file). All
AWS/CFBD calls are mocked.
"""
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from library.storage.ncaafb_coach_cache import coach_lookup_by_school, get_cached_coaches, rank_lookup_by_school

BUCKET = "test-bucket"


def _make_s3():
    mock_s3 = MagicMock()
    store: dict[str, bytes] = {}

    def _get(**kwargs):
        key = kwargs.get("Key")
        if key in store:
            return {"Body": BytesIO(store[key])}
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject")

    def _put(**kwargs):
        body = kwargs.get("Body")
        store[kwargs.get("Key")] = body.encode("utf-8") if isinstance(body, str) else body

    mock_s3.get_object.side_effect = _get
    mock_s3.put_object.side_effect = _put
    return mock_s3


def _prime_cache(mock_s3, key: str, data, fetched_at: str = "2026-01-01T00:00:00+00:00") -> None:
    mock_s3.put_object(Key=key, Body=json.dumps({"fetched_at": fetched_at, "data": data}))


def _coach(first, last, school, year, wins, losses, ties=0, hire_year=None):
    return {
        "firstName": first, "lastName": last,
        "hireDate": f"{hire_year}-01-15" if hire_year else None,
        "seasons": [{"school": school, "year": year, "wins": wins, "losses": losses, "ties": ties}],
    }


class TestGetCachedCoaches:
    def test_cache_miss_fetches_and_caches(self):
        mock_s3 = _make_s3()
        client = MagicMock()
        client.get_coaches.return_value = [_coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016)]

        result = get_cached_coaches(mock_s3, BUCKET, client, 2025)

        assert result == [_coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016)]
        client.get_coaches.assert_called_once_with(2025)
        mock_s3.put_object.assert_called_once()

    def test_fresh_cache_hit_does_not_call_the_client(self):
        mock_s3 = _make_s3()
        fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _prime_cache(mock_s3, "ncaafb/cache/season-coaches/2025.json", [{"firstName": "cached"}], fetched_at=fresh)
        client = MagicMock()

        result = get_cached_coaches(mock_s3, BUCKET, client, 2025)

        assert result == [{"firstName": "cached"}]
        client.get_coaches.assert_not_called()

    def test_stale_cache_refetches(self):
        mock_s3 = _make_s3()
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _prime_cache(mock_s3, "ncaafb/cache/season-coaches/2025.json", [{"firstName": "stale"}], fetched_at=stale)
        client = MagicMock()
        client.get_coaches.return_value = [_coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016)]

        get_cached_coaches(mock_s3, BUCKET, client, 2025)

        client.get_coaches.assert_called_once_with(2025)


class TestCoachLookupBySchool:
    def test_keys_by_school_for_matching_season(self):
        coaches = [_coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016)]
        lookup = coach_lookup_by_school(coaches, 2025)
        assert "Georgia" in lookup
        assert lookup["Georgia"]["coach_name"] == "Kirby Smart"

    def test_experience_derived_from_hire_date_year(self):
        coaches = [_coach("Kirby", "Smart", "Georgia", 2025, 11, 1, hire_year=2016)]
        lookup = coach_lookup_by_school(coaches, 2025)
        assert lookup["Georgia"]["coach_experience"] == 9

    def test_season_win_pct_computed_from_wins_losses_ties(self):
        coaches = [_coach("Kirby", "Smart", "Georgia", 2025, 9, 1, ties=0, hire_year=2016)]
        lookup = coach_lookup_by_school(coaches, 2025)
        assert lookup["Georgia"]["season_win_pct"] == 0.9

    def test_season_win_pct_none_when_no_games_played_yet(self):
        coaches = [_coach("Kirby", "Smart", "Georgia", 2025, 0, 0, hire_year=2016)]
        lookup = coach_lookup_by_school(coaches, 2025)
        assert lookup["Georgia"]["season_win_pct"] is None

    def test_coach_with_no_matching_season_year_is_skipped(self):
        coaches = [_coach("Kirby", "Smart", "Georgia", 2024, 12, 2, hire_year=2016)]
        lookup = coach_lookup_by_school(coaches, 2025)
        assert lookup == {}

    def test_coach_missing_hire_date_has_none_experience(self):
        coach = _coach("Kirby", "Smart", "Georgia", 2025, 11, 1)
        coach["hireDate"] = None
        lookup = coach_lookup_by_school([coach], 2025)
        assert lookup["Georgia"]["coach_experience"] is None


class TestRankLookupBySchool:
    def test_returns_ap_top_25_school_to_rank(self):
        rankings = [{"polls": [
            {"poll": "Coaches Poll", "ranks": [{"school": "Alabama", "rank": 1}]},
            {"poll": "AP Top 25", "ranks": [{"school": "Georgia", "rank": 1}, {"school": "Ohio State", "rank": 2}]},
        ]}]
        result = rank_lookup_by_school(rankings)
        assert result == {"Georgia": 1, "Ohio State": 2}

    def test_ignores_non_ap_polls_in_the_same_payload(self):
        # FCS/other-division polls are bundled in the same response --
        # must not be mistaken for the FBS AP Top 25.
        rankings = [{"polls": [{"poll": "FCS Coaches Poll", "ranks": [{"school": "Montana", "rank": 1}]}]}]
        result = rank_lookup_by_school(rankings)
        assert result == {}

    def test_empty_when_no_weeks_returned(self):
        assert rank_lookup_by_school([]) == {}
