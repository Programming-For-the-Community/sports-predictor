"""
Unit tests for library.storage.pga_season_stats -- moved unchanged from
tests/feature-engineering/pga/test_build_dataset.py 2026-08-27 when
load_season_stat_snapshots/resolve_season_stats were extracted out of
build_dataset.py so aws-lambdas/pga/predict/live_features.py could reuse
them without importing a Fargate-task module.
"""
from unittest.mock import MagicMock

from library.features.pga import SEASON_STAT_CATEGORIES
from library.storage import pga_season_stats


def _statistics_payload(categories: dict):
    """categories: {raw_category_name: {athlete_id: value}}."""
    return {"stats": {"categories": [
        {"name": name, "leaders": [{"athlete": {"id": athlete_id}, "value": value} for athlete_id, value in values.items()]}
        for name, values in categories.items()
    ]}}


class TestLoadSeasonStatSnapshots:
    def _raw_s3(self, keys_and_payloads: dict):
        raw_s3 = MagicMock()
        raw_s3.list_keys.return_value = list(keys_and_payloads.keys())
        raw_s3.get_json.side_effect = lambda key: keys_and_payloads[key]
        return raw_s3

    def test_parses_a_date_keyed_snapshot(self):
        raw_s3 = self._raw_s3({
            "pga/statistics/20260601.json": _statistics_payload({"yardsPerDrive": {"9478": 320.1}}),
        })

        snapshots = pga_season_stats.load_season_stat_snapshots(raw_s3)

        assert len(snapshots) == 1
        assert snapshots[0]["as_of_date"] == "2026-06-01"
        assert snapshots[0]["value_by_category_and_athlete"]["yardsPerDrive"]["9478"] == 320.1

    def test_ignores_categories_outside_the_project_supported_set(self):
        raw_s3 = self._raw_s3({
            "pga/statistics/20260601.json": _statistics_payload({
                "yardsPerDrive": {"9478": 320.1}, "officialAmount": {"9478": 1000000},
            }),
        })

        snapshots = pga_season_stats.load_season_stat_snapshots(raw_s3)

        assert "officialAmount" not in snapshots[0]["value_by_category_and_athlete"]

    def test_ignores_a_non_matching_key(self):
        raw_s3 = self._raw_s3({"pga/statistics/notadate.json": {}})

        snapshots = pga_season_stats.load_season_stat_snapshots(raw_s3)

        assert snapshots == []

    def test_returns_snapshots_sorted_oldest_first(self):
        raw_s3 = self._raw_s3({
            "pga/statistics/20260801.json": _statistics_payload({}),
            "pga/statistics/20260601.json": _statistics_payload({}),
        })

        snapshots = pga_season_stats.load_season_stat_snapshots(raw_s3)

        assert [s["as_of_date"] for s in snapshots] == ["2026-06-01", "2026-08-01"]


class TestResolveSeasonStats:
    def test_picks_the_most_recent_snapshot_strictly_before_the_event_date(self):
        snapshots = [
            {"as_of_date": "2026-06-01", "value_by_category_and_athlete": {"yardsPerDrive": {"9478": 300.0}}},
            {"as_of_date": "2026-07-01", "value_by_category_and_athlete": {"yardsPerDrive": {"9478": 310.0}}},
        ]

        resolved = pga_season_stats.resolve_season_stats(snapshots, "9478", "2026-08-01")

        assert resolved["yardsPerDrive"] == 310.0

    def test_never_uses_a_same_day_or_later_snapshot(self):
        snapshots = [{"as_of_date": "2026-08-01", "value_by_category_and_athlete": {"yardsPerDrive": {"9478": 310.0}}}]

        resolved = pga_season_stats.resolve_season_stats(snapshots, "9478", "2026-08-01")

        assert resolved["yardsPerDrive"] is None

    def test_returns_none_for_every_category_when_no_snapshot_qualifies(self):
        resolved = pga_season_stats.resolve_season_stats([], "9478", "2026-08-01")

        assert all(value is None for value in resolved.values())
        assert set(resolved.keys()) == set(SEASON_STAT_CATEGORIES)

    def test_is_none_for_a_golfer_not_in_that_categorys_top_50(self):
        snapshots = [{"as_of_date": "2026-06-01", "value_by_category_and_athlete": {"yardsPerDrive": {"9478": 300.0}}}]

        resolved = pga_season_stats.resolve_season_stats(snapshots, "99999", "2026-08-01")

        assert resolved["yardsPerDrive"] is None
