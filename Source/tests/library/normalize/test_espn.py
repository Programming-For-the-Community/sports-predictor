"""
Unit tests for library.normalize.espn's boxscore_to_player_game_stats
field-naming logic. Hand-built synthetic payloads, not live ESPN data --
the live-network shape checks live in
tests/data-backfills/nfl/test_espn_nfl.py. This suite exists specifically
to lock in the category-prefix de-duplication rule: getting it wrong
previously meant a QB's passing stats landed under keys
(library.features.nfl's build_event_features) never checked for, which
silently zeroed out those columns in production.
"""
from library.normalize.espn import boxscore_to_player_game_stats


def _summary(statistics):
    return {
        "header": {"id": "E1", "competitions": [{"date": "2025-09-07T17:00Z"}]},
        "boxscore": {"players": [{"team": {"id": "KC"}, "statistics": statistics}]},
    }


def _stat_line(statistics):
    stats_items, _ = boxscore_to_player_game_stats(_summary(statistics), "nfl", compound_key_splits={})
    return stats_items[0]["stat_line"]


class TestBoxscoreToPlayerGameStatsFieldNames:
    def test_key_already_carrying_the_category_is_not_double_prefixed(self):
        # ESPN's own key ("passingYards") already bakes in the category
        # name -- the field name should be "passing_yards", not
        # "passing_passing_yards".
        statistics = [{
            "name": "passing",
            "keys": ["passingYards", "passingTouchdowns"],
            "athletes": [{"athlete": {"id": "qb1", "displayName": "QB One"}, "stats": ["300", "2"]}],
        }]

        line = _stat_line(statistics)

        assert line == {"passing_yards": 300, "passing_touchdowns": 2}

    def test_bare_key_in_a_non_matching_category_still_gets_prefixed(self):
        # "interceptions" is bare (doesn't carry a category name) in both
        # the "passing" category (thrown picks) and the "interceptions"
        # category (defensive picks). The "passing" one must stay
        # prefixed ("passing_interceptions") so it doesn't collide with
        # the "interceptions" category's own reduced-to-bare key on a
        # player who somehow has both in the same game.
        statistics = [
            {
                "name": "passing", "keys": ["interceptions"],
                "athletes": [{"athlete": {"id": "p1", "displayName": "Player One"}, "stats": ["1"]}],
            },
            {
                "name": "interceptions", "keys": ["interceptions"],
                "athletes": [{"athlete": {"id": "p1", "displayName": "Player One"}, "stats": ["2"]}],
            },
        ]

        line = _stat_line(statistics)

        assert line == {"passing_interceptions": 1, "interceptions": 2}

    def test_category_exact_match_key_is_not_prefixed(self):
        # The "interceptions" category's own bare "interceptions" key, in
        # isolation (no colliding "passing" category present), reduces to
        # the clean "interceptions" rather than "interceptions_interceptions".
        statistics = [{
            "name": "interceptions",
            "keys": ["interceptions"],
            "athletes": [{"athlete": {"id": "p1", "displayName": "Player One"}, "stats": ["2"]}],
        }]

        line = _stat_line(statistics)

        assert line == {"interceptions": 2}

    def test_unrelated_key_gets_the_category_prefix(self):
        statistics = [{
            "name": "defensive",
            "keys": ["totalTackles"],
            "athletes": [{"athlete": {"id": "p1", "displayName": "Player One"}, "stats": ["7"]}],
        }]

        line = _stat_line(statistics)

        assert line == {"defensive_total_tackles": 7}
