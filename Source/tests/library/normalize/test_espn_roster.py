"""
Unit tests for library.normalize.espn.roster_to_player_entities. Hand-
built synthetic payloads matching a real live ESPN roster response
shape (site.api.espn.com/.../teams/{id}/roster) -- athletes groups
players by position ("offense"/"defense"/"specialTeam"/
"injuredReserveOrOut"/"suspended"/"practiceSquad"), each group holding a
flat "items" list. Split out of what used to be one large test_espn.py --
see test_espn_player_game_stats.py's own history note.
"""
from library.normalize.espn import roster_to_player_entities, roster_to_team_injuries


def _roster(*, team_id="23", timestamp="2026-08-08T05:05:15Z", groups):
    return {"team": {"id": team_id}, "timestamp": timestamp, "athletes": groups}


def _athlete(athlete_id, name="Athlete Name", jersey="10", position="QB"):
    return {"id": athlete_id, "displayName": name, "jersey": jersey, "position": {"abbreviation": position}}


class TestRosterToPlayerEntities:
    def test_returns_one_entity_per_athlete_across_every_position_group(self):
        roster = _roster(groups=[
            {"position": "offense", "items": [_athlete("1"), _athlete("2")]},
            {"position": "defense", "items": [_athlete("3")]},
        ])

        entities = roster_to_player_entities(roster, "nfl")

        assert {e["entity_id"] for e in entities} == {"1", "2", "3"}

    def test_includes_injured_reserve_and_practice_squad_players(self):
        # These are still on the team, not some other one -- exactly the
        # fact this function exists to keep current.
        roster = _roster(groups=[
            {"position": "injuredReserveOrOut", "items": [_athlete("1")]},
            {"position": "practiceSquad", "items": [_athlete("2")]},
        ])

        entities = roster_to_player_entities(roster, "nfl")

        assert {e["entity_id"] for e in entities} == {"1", "2"}

    def test_team_id_as_of_comes_from_the_payloads_own_timestamp_truncated_to_a_date(self):
        roster = _roster(timestamp="2026-08-08T05:05:15Z", groups=[
            {"position": "offense", "items": [_athlete("1")]},
        ])

        entities = roster_to_player_entities(roster, "nfl")

        assert entities[0]["metadata"]["team_id_as_of"] == "2026-08-08"

    def test_entity_carries_team_id_sport_and_name(self):
        roster = _roster(team_id="23", groups=[
            {"position": "offense", "items": [_athlete("1", name="Drew Allar", jersey="8", position="QB")]},
        ])

        entities = roster_to_player_entities(roster, "nfl")

        entity = entities[0]
        assert entity["sport"] == "nfl"
        assert entity["entity_type"] == "player"
        assert entity["name"] == "Drew Allar"
        assert entity["metadata"]["team_id"] == "23"
        assert entity["metadata"]["jersey"] == "8"
        assert entity["metadata"]["position"] == "QB"
        assert entity["team_key"] == "SPORT#NFL#TEAM#23"

    def test_entity_position_missing_from_espn_payload_is_none(self):
        roster = _roster(groups=[{"position": "offense", "items": [{"id": "1", "displayName": "No Position"}]}])

        entities = roster_to_player_entities(roster, "nfl")

        assert entities[0]["metadata"]["position"] is None

    def test_empty_roster_returns_no_entities(self):
        assert roster_to_player_entities(_roster(groups=[]), "nfl") == []


class TestRosterToPlayerEntitiesFlatShape:
    """NBA's roster response has no NFL-style position-group wrapper --
    `athletes` is a flat list of athlete dicts directly (confirmed live,
    2026-08-14). Same function, same output shape -- only the input's
    grouping differs, detected structurally (see _flatten_roster_athletes'
    own docstring), not branched on sport."""

    def test_returns_one_entity_per_athlete_with_no_grouping_wrapper(self):
        roster = _roster(groups=[_athlete("1", position="F"), _athlete("2", position="G")])

        entities = roster_to_player_entities(roster, "nba")

        assert {e["entity_id"] for e in entities} == {"1", "2"}
        assert {e["metadata"]["position"] for e in entities} == {"F", "G"}


def _athlete_with_injury(athlete_id, status):
    athlete = _athlete(athlete_id)
    athlete["injuries"] = [{"status": status}]
    return athlete


class TestRosterToTeamInjuries:
    def test_returns_entity_id_and_status_for_a_current_injury(self):
        roster = _roster(groups=[_athlete_with_injury("1", "Out")])

        injuries = roster_to_team_injuries(roster)

        assert injuries == [{"entity_id": "1", "status": "Out"}]

    def test_healthy_athlete_with_no_injuries_key_is_omitted(self):
        roster = _roster(groups=[_athlete("1")])

        assert roster_to_team_injuries(roster) == []

    def test_athlete_with_empty_injuries_list_is_omitted(self):
        athlete = _athlete("1")
        athlete["injuries"] = []
        roster = _roster(groups=[athlete])

        assert roster_to_team_injuries(roster) == []

    def test_non_current_status_is_filtered_out(self):
        # A recovered player's status-change log entry shouldn't still
        # count as a current injury (same reasoning
        # EspnCoreApiClient.get_team_injuries' own filter documents).
        roster = _roster(groups=[_athlete_with_injury("1", "Active")])

        assert roster_to_team_injuries(roster) == []

    def test_falls_back_to_nested_type_description_when_status_key_absent(self):
        athlete = _athlete("1")
        athlete["injuries"] = [{"type": {"description": "Doubtful"}}]
        roster = _roster(groups=[athlete])

        assert roster_to_team_injuries(roster) == [{"entity_id": "1", "status": "Doubtful"}]

    def test_works_with_nfl_style_grouped_roster_too(self):
        roster = {
            "team": {"id": "1"}, "timestamp": "2026-01-01T00:00Z",
            "athletes": [{"position": "offense", "items": [_athlete_with_injury("1", "Questionable")]}],
        }

        assert roster_to_team_injuries(roster) == [{"entity_id": "1", "status": "Questionable"}]
