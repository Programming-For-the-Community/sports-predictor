"""
Unit tests for library.normalize.odds. The payloads are trimmed from real
ESPN responses: the scoreboard's ATL at ORL (2026-10-21) and the core
API's OKC at SAS (401909090, after it went final).
"""
import logging
from unittest.mock import MagicMock

from library.normalize import odds

SCOREBOARD_COMPETITION = {
    "odds": [{
        "provider": {"id": "100", "name": "Draft Kings", "priority": 1},
        "details": "ORL -2.5",
        "spread": -2.5,
        "awayTeamOdds": {"favorite": False, "underdog": True, "team": {"id": "1", "abbreviation": "ATL"}},
        "homeTeamOdds": {"favorite": True, "underdog": False, "team": {"id": "19", "abbreviation": "ORL"}},
        "moneyline": {
            "home": {"close": {"odds": "-130"}, "open": {"odds": "-135"}},
            "away": {"close": {"odds": "+110"}, "open": {"odds": "+114"}},
        },
    }],
}

CORE_LISTING = {
    "count": 1,
    "items": [{
        "provider": {"id": "100", "name": "DraftKings", "priority": 1},
        "details": "OKC -1.5",
        "homeTeamOdds": {"favorite": False, "underdog": True, "moneyLine": -102},
        "awayTeamOdds": {"favorite": True, "underdog": False, "moneyLine": -118},
    }],
}


def _logger():
    return logging.getLogger("test-odds")


class TestScoreboardOdds:
    def test_reads_the_favorite_flag_and_closing_moneylines(self):
        assert odds.scoreboard_odds(SCOREBOARD_COMPETITION) == {
            "provider": "Draft Kings", "details": "ORL -2.5", "favorite": "home",
            "home_moneyline": -130, "away_moneyline": 110,
        }

    def test_falls_back_to_the_opening_line(self):
        competition = {"odds": [{
            **SCOREBOARD_COMPETITION["odds"][0],
            "moneyline": {"home": {"open": {"odds": "-135"}}, "away": {"open": {"odds": "+114"}}},
        }]}
        result = odds.scoreboard_odds(competition)
        assert (result["home_moneyline"], result["away_moneyline"]) == (-135, 114)

    def test_none_without_odds(self):
        assert odds.scoreboard_odds({}) is None
        assert odds.scoreboard_odds({"odds": []}) is None


class TestCoreOdds:
    def test_reads_a_finished_games_closing_line(self):
        assert odds.core_odds(CORE_LISTING) == {
            "provider": "DraftKings", "details": "OKC -1.5", "favorite": "away",
            "home_moneyline": -102, "away_moneyline": -118,
        }

    def test_prefers_draftkings_among_several_books(self):
        other = {**CORE_LISTING["items"][0], "provider": {"name": "Caesars"}, "details": "OKC -2"}
        listing = {"items": [other, CORE_LISTING["items"][0]]}
        assert odds.core_odds(listing)["details"] == "OKC -1.5"

    def test_none_for_an_empty_listing(self):
        assert odds.core_odds({"items": []}) is None


class TestFavorite:
    def test_shorter_moneyline_when_neither_side_is_flagged(self):
        listing = {"items": [{"details": "MTL -225", "homeTeamOdds": {"moneyLine": -225}, "awayTeamOdds": {"moneyLine": 185}}]}
        assert odds.core_odds(listing)["favorite"] == "home"

    def test_a_pickem_has_no_favorite(self):
        listing = {"items": [{"details": "EVEN", "homeTeamOdds": {"moneyLine": -110}, "awayTeamOdds": {"moneyLine": -110}}]}
        assert odds.core_odds(listing)["favorite"] is None

    def test_both_sides_flagged_falls_back_to_the_moneyline(self):
        listing = {"items": [{
            "details": "X", "homeTeamOdds": {"favorite": True, "moneyLine": 120}, "awayTeamOdds": {"favorite": True, "moneyLine": -140},
        }]}
        assert odds.core_odds(listing)["favorite"] == "away"


class TestMoneylineParsing:
    def test_even_reads_as_plus_100(self):
        assert odds._moneyline("EVEN") == 100

    def test_unreadable_values_are_none(self):
        for value in ("OFF", "", None, True, {"american": "-110"}):
            assert odds._moneyline(value) is None


class TestFetchEventOdds:
    def test_saves_new_lines_and_skips_ones_already_saved(self):
        client = MagicMock()
        client.get_event_odds.return_value = CORE_LISTING
        put_json = MagicMock()

        saved, failed = odds.fetch_event_odds(
            client, "nba", ["1", "2"], object_exists=lambda key: key == "nba/odds/1.json", put_json=put_json, logger=_logger(),
        )

        assert (saved, failed) == (1, 0)
        client.get_event_odds.assert_called_once_with("2")
        put_json.assert_called_once_with("nba/odds/2.json", CORE_LISTING)

    def test_refresh_refetches_a_saved_line(self):
        client = MagicMock()
        client.get_event_odds.return_value = CORE_LISTING

        saved, _ = odds.fetch_event_odds(
            client, "ncaafb", ["1"], object_exists=lambda key: True, put_json=MagicMock(), logger=_logger(), refresh=True,
        )

        assert saved == 1

    def test_a_listing_with_no_line_yet_is_not_saved(self):
        client = MagicMock()
        client.get_event_odds.return_value = {"count": 0, "items": []}
        put_json = MagicMock()

        assert odds.fetch_event_odds(
            client, "nba", ["1"], object_exists=lambda key: False, put_json=put_json, logger=_logger(),
        ) == (0, 0)
        put_json.assert_not_called()

    def test_one_failed_fetch_does_not_stop_the_rest(self):
        client = MagicMock()
        client.get_event_odds.side_effect = lambda event_id: (_ for _ in ()).throw(RuntimeError("boom")) if event_id == "1" else CORE_LISTING

        saved, failed = odds.fetch_event_odds(
            client, "nba", ["1", "2"], object_exists=lambda key: False, put_json=MagicMock(), logger=_logger(), max_workers=2,
        )

        assert (saved, failed) == (1, 1)


class TestApplyCoreOdds:
    def test_saves_the_line_on_the_event_named_by_the_key(self):
        storage = MagicMock()
        storage.set_event_odds.return_value = True

        odds.apply_core_odds(storage, "nba", "nba/odds/401909090.json", CORE_LISTING, _logger())

        storage.set_event_odds.assert_called_once_with("SPORT#NBA#EVENT#401909090", odds.core_odds(CORE_LISTING))

    def test_nothing_saved_without_a_line(self):
        storage = MagicMock()

        odds.apply_core_odds(storage, "nba", "nba/odds/1.json", {"items": []}, _logger())

        storage.set_event_odds.assert_not_called()
