"""
Unit tests for library.normalize.espn.boxscore_to_player_game_stats,
including its field-naming logic (category-prefix de-duplication).
Hand-built synthetic payloads, not live ESPN data -- the live-network
shape checks live in tests/data-backfills/nfl/test_espn_nfl.py. The
field-naming suite exists specifically to lock in the de-duplication
rule: getting it wrong previously meant a QB's passing stats landed under
keys (library.features.nfl's build_event_features) never checked for,
which silently zeroed out those columns in production. Split out of what
used to be one large test_espn.py -- see test_espn_roster.py,
test_espn_team_game_stats.py, and test_espn_scoreboard_event.py for this
file's siblings, one per concern.
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


class TestBoxscoreToPlayerGameStats:
    def test_item_carries_sport_directly_not_just_embedded_in_event_key(self):
        # What lets get_all_player_game_stats Query the sport-index GSI
        # instead of scanning the whole (eventually multi-sport) table.
        stats_items, _ = boxscore_to_player_game_stats(
            _summary([{"name": "passing", "keys": ["passingYards"], "athletes": [
                {"athlete": {"id": "1", "displayName": "QB One"}, "stats": ["250"]},
            ]}]),
            "nfl", compound_key_splits={},
        )

        assert stats_items[0]["sport"] == "nfl"

    def test_player_entity_carries_team_key(self):
        # What lets get_team_entities Query the team-index GSI (see
        # live_features.py's roster-driven presumptive-leader selection).
        # _summary's team is hardcoded to "KC".
        _, player_entities = boxscore_to_player_game_stats(
            _summary([{"name": "passing", "keys": ["passingYards"], "athletes": [
                {"athlete": {"id": "1", "displayName": "QB One"}, "stats": ["250"]},
            ]}]),
            "nfl", compound_key_splits={},
        )

        assert player_entities[0]["team_key"] == "SPORT#NFL#TEAM#KC"

    def test_player_entity_carries_position(self):
        # Same metadata.position field/shape roster_to_player_entities sets
        # -- upsert_player_entity replaces the whole entity item, not just
        # the fields a given source knows about, so this entity omitting
        # position would blank out whatever a prior roster sync had already
        # set for this player (live_features.py's roster-driven leader
        # selection depends on it being there for an actively-playing
        # player, not just a freshly-rostered one).
        _, player_entities = boxscore_to_player_game_stats(
            _summary([{"name": "passing", "keys": ["passingYards"], "athletes": [
                {"athlete": {"id": "1", "displayName": "QB One", "position": {"abbreviation": "QB"}}, "stats": ["250"]},
            ]}]),
            "nfl", compound_key_splits={},
        )

        assert player_entities[0]["metadata"]["position"] == "QB"

    def test_player_entity_position_missing_from_espn_payload_is_none(self):
        _, player_entities = boxscore_to_player_game_stats(
            _summary([{"name": "passing", "keys": ["passingYards"], "athletes": [
                {"athlete": {"id": "1", "displayName": "QB One"}, "stats": ["250"]},
            ]}]),
            "nfl", compound_key_splits={},
        )

        assert player_entities[0]["metadata"]["position"] is None


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

    def test_category_with_no_name_field_gets_no_prefix_at_all(self):
        # NBA's single player-stat block has no "name" field at all (only
        # a display-label "names" array) -- confirmed live, 2026-08-14.
        # Falling back to a fabricated "misc" prefix would silently rename
        # "points" to "misc_points", breaking TARGET_STAT's exact
        # stat_line key match. A category missing "name" entirely (not a
        # category whose name happens to be "misc") gets no prefix.
        statistics = [{
            "keys": ["points", "rebounds"],
            "athletes": [{"athlete": {"id": "p1", "displayName": "Player One"}, "stats": ["17", "4"]}],
        }]

        line = _stat_line(statistics)

        assert line == {"points": 17, "rebounds": 4}
