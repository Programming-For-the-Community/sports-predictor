"""
Unit tests for library.normalize.espn's boxscore_to_player_game_stats
field-naming logic and boxscore_to_team_game_stats. Hand-built synthetic
payloads, not live ESPN data -- the live-network shape checks live in
tests/data-backfills/nfl/test_espn_nfl.py. The player-stats suite exists
specifically to lock in the category-prefix de-duplication rule: getting
it wrong previously meant a QB's passing stats landed under keys
(library.features.nfl's build_event_features) never checked for, which
silently zeroed out those columns in production.
"""
from library.normalize.espn import boxscore_to_player_game_stats, boxscore_to_team_game_stats


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


TEAM_COMPOUND_KEY_SPLITS = {
    "thirdDownEff": ("third_down_conversions", "third_down_attempts"),
    "completionAttempts": ("completions", "pass_attempts"),
    "sacksYardsLost": ("sacks_taken", "sack_yards_lost"),
}


def _team_summary(team_statistics):
    return {
        "header": {"id": "E1", "competitions": [{"date": "2025-09-07T17:00Z"}]},
        "boxscore": {"teams": team_statistics},
    }


class TestBoxscoreToTeamGameStats:
    def test_returns_one_item_per_team(self):
        summary = _team_summary([
            {"team": {"id": "9"}, "statistics": [{"name": "turnovers", "displayValue": "0"}]},
            {"team": {"id": "1"}, "statistics": [{"name": "turnovers", "displayValue": "1"}]},
        ])

        items = boxscore_to_team_game_stats(summary, "nfl", TEAM_COMPOUND_KEY_SPLITS)

        assert len(items) == 2
        assert {item["team_id"] for item in items} == {"9", "1"}
        assert items[0]["team_key"] == "TEAM#9"
        assert items[0]["event_key"] == "SPORT#NFL#EVENT#E1"
        assert items[0]["event_date"] == "2025-09-07"

    def test_flat_stat_name_is_snake_cased_directly_no_category_prefix(self):
        # Team stats are a flat list (no category grouping like player
        # stats), so there's no double-prefix collision risk to guard
        # against here -- every name snake-cases to a unique field as-is.
        summary = _team_summary([
            {"team": {"id": "9"}, "statistics": [{"name": "netPassingYards", "displayValue": "235"}]},
        ])

        items = boxscore_to_team_game_stats(summary, "nfl", TEAM_COMPOUND_KEY_SPLITS)

        assert items[0]["stat_line"] == {"net_passing_yards": 235}

    def test_compound_value_is_split_into_two_fields(self):
        summary = _team_summary([
            {"team": {"id": "9"}, "statistics": [{"name": "thirdDownEff", "displayValue": "3-9"}]},
        ])

        items = boxscore_to_team_game_stats(summary, "nfl", TEAM_COMPOUND_KEY_SPLITS)

        assert items[0]["stat_line"] == {"third_down_conversions": 3, "third_down_attempts": 9}

    def test_slash_separated_compound_value_is_split_correctly(self):
        summary = _team_summary([
            {"team": {"id": "9"}, "statistics": [{"name": "completionAttempts", "displayValue": "14/25"}]},
        ])

        items = boxscore_to_team_game_stats(summary, "nfl", TEAM_COMPOUND_KEY_SPLITS)

        assert items[0]["stat_line"] == {"completions": 14, "pass_attempts": 25}

    def test_possession_time_is_parsed_to_seconds(self):
        summary = _team_summary([
            {"team": {"id": "9"}, "statistics": [{"name": "possessionTime", "displayValue": "23:45"}]},
        ])

        items = boxscore_to_team_game_stats(summary, "nfl", TEAM_COMPOUND_KEY_SPLITS)

        assert items[0]["stat_line"] == {"possession_time_seconds": 1425}

    def test_duplicate_stat_name_with_matching_value_collapses_harmlessly(self):
        # ESPN's own team statistics list really does contain
        # "interceptions" twice with an identical value both times (a
        # quirk in their data, verified against a real response) -- this
        # must not raise or produce two conflicting entries.
        summary = _team_summary([
            {"team": {"id": "9"}, "statistics": [
                {"name": "interceptions", "displayValue": "1"},
                {"name": "interceptions", "displayValue": "1"},
            ]},
        ])

        items = boxscore_to_team_game_stats(summary, "nfl", TEAM_COMPOUND_KEY_SPLITS)

        assert items[0]["stat_line"] == {"interceptions": 1}
