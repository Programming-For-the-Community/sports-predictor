"""Unit tests for library.storage.cfbd_supplements."""
from unittest.mock import MagicMock

from library.storage import cfbd_supplements


def _raw_s3(objects: dict):
    raw = MagicMock()
    raw.list_keys.side_effect = lambda prefix: [k for k in objects if k.startswith(prefix)]
    raw.get_json.side_effect = lambda key: objects[key]
    return raw


OBJECTS = {
    "ncaafb/advanced/2025/regular/9.json": [
        {"gameId": 401, "team": "Georgia", "team_id": "61", "offense": {"plays": 70}},
        {"gameId": 401, "team": "Somewhere", "offense": {}},  # untagged -- unknown school
    ],
    "ncaafb/advanced/2023/regular/9.json": [{"gameId": 300, "team_id": "61"}],
    "ncaafb/talent/2025.json": [{"team": "Georgia", "team_id": "61", "talent": 990.5}, {"team_id": "62", "talent": "n/a"}],
    "ncaafb/returning/2025.json": [{"team": "Georgia", "team_id": "61", "percentPPA": 0.55}],
}


class TestLoad:
    def test_indexes_each_supplement_by_our_keys(self):
        supplements = cfbd_supplements.load(_raw_s3(OBJECTS))
        assert supplements.advanced[("SPORT#NCAAFB#EVENT#401", "61")]["offense"] == {"plays": 70}
        assert len(supplements.advanced) == 2
        assert supplements.talent == {(2025, "61"): 990.5}
        assert supplements.returning[(2025, "61")]["percentPPA"] == 0.55

    def test_limits_to_the_given_seasons(self):
        supplements = cfbd_supplements.load(_raw_s3(OBJECTS), {2025})
        assert list(supplements.advanced) == [("SPORT#NCAAFB#EVENT#401", "61")]

    def test_cached_load_reads_once(self):
        cfbd_supplements._cache.clear()
        raw = _raw_s3(OBJECTS)
        cfbd_supplements.load_cached(raw, {2025})
        cfbd_supplements.load_cached(raw, {2025})
        assert raw.list_keys.call_count == 3  # one listing per prefix, once


class TestAttachAdvanced:
    def test_each_row_gets_its_own_teams_entry(self):
        supplements = cfbd_supplements.load(_raw_s3(OBJECTS))
        rows = cfbd_supplements.attach_advanced(
            [{"event_key": "SPORT#NCAAFB#EVENT#401", "team_id": "61"}, {"event_key": "SPORT#NCAAFB#EVENT#401", "team_id": "99"}],
            supplements,
        )
        assert rows[0]["advanced"]["offense"] == {"plays": 70}
        assert rows[1]["advanced"] is None


def test_keys():
    assert cfbd_supplements.advanced_key(2025, "regular", 9) == "ncaafb/advanced/2025/regular/9.json"
    assert cfbd_supplements.talent_key(2025) == "ncaafb/talent/2025.json"
    assert cfbd_supplements.returning_key(2025) == "ncaafb/returning/2025.json"
