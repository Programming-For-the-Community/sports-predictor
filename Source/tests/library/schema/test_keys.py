"""
Unit tests for library.schema.keys -- the DynamoDB key builders every
sport's normalize step uses. No dedicated coverage existed before the
player_key sport-scoping fix; adding it now rather than leaving this
module's key format implicit.
"""
from library.schema.keys import entity_key, event_key, player_key, team_key


class TestEntityKey:
    def test_builds_sport_scoped_key(self):
        assert entity_key("nfl", "12483") == "SPORT#NFL#ENTITY#12483"


class TestEventKey:
    def test_builds_sport_scoped_key(self):
        assert event_key("nfl", "401547417") == "SPORT#NFL#EVENT#401547417"


class TestPlayerKey:
    def test_builds_sport_scoped_key(self):
        # Sport-scoped like entity_key/event_key -- player_game_stats and
        # every other table sharing entity_id-based identifiers are
        # shared across every sport (see Terraform/locals.tf's table
        # names, which have no sport suffix), so an unscoped player_key
        # risks two different sports' athletes colliding under the same
        # raw ESPN-style numeric id once a second sport lands.
        assert player_key("nfl", "12483") == "SPORT#NFL#PLAYER#12483"

    def test_different_sports_never_collide_for_the_same_raw_id(self):
        assert player_key("nfl", "12483") != player_key("nba", "12483")


class TestTeamKey:
    def test_builds_key(self):
        assert team_key("12") == "TEAM#12"
