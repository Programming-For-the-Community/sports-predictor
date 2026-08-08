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
from library.normalize.espn import (
    boxscore_to_player_game_stats,
    boxscore_to_team_game_stats,
    roster_to_player_entities,
    scoreboard_event_to_event_item,
)


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


def _roster(*, team_id="23", timestamp="2026-08-08T05:05:15Z", groups):
    return {"team": {"id": team_id}, "timestamp": timestamp, "athletes": groups}


def _athlete(athlete_id, name="Athlete Name", jersey="10"):
    return {"id": athlete_id, "displayName": name, "jersey": jersey}


# Shape verified against a real live ESPN roster response
# (site.api.espn.com/.../teams/{id}/roster) -- athletes groups players by
# position ("offense"/"defense"/"specialTeam"/"injuredReserveOrOut"/
# "suspended"/"practiceSquad"), each group holding a flat "items" list.
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
            {"position": "offense", "items": [_athlete("1", name="Drew Allar", jersey="8")]},
        ])

        entities = roster_to_player_entities(roster, "nfl")

        entity = entities[0]
        assert entity["sport"] == "nfl"
        assert entity["entity_type"] == "player"
        assert entity["name"] == "Drew Allar"
        assert entity["metadata"]["team_id"] == "23"
        assert entity["metadata"]["jersey"] == "8"

    def test_empty_roster_returns_no_entities(self):
        assert roster_to_player_entities(_roster(groups=[]), "nfl") == []


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
        # Stored directly now, not just embedded in event_key -- what lets
        # get_all_team_game_stats Query the sport-index GSI instead of
        # scanning the whole (eventually multi-sport) table.
        assert items[0]["sport"] == "nfl"

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


def _scoreboard_event(event_id="401547417", home_id="12", away_id="24", **extra):
    return {
        "id": event_id,
        "date": "2025-09-28T20:25Z",
        "status": {"type": {"completed": False}},
        "season": {"year": 2025, "type": 2},
        "week": {"number": 4},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id}, "score": "0", "winner": False},
                {"homeAway": "away", "team": {"id": away_id}, "score": "0", "winner": False},
            ],
        }],
        **extra,
    }


class TestScoreboardEventToEventItem:
    def test_event_date_is_truncated_to_the_date(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")
        assert item["event_date"] == "2025-09-28"

    def test_kickoff_time_keeps_the_full_timestamp(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")
        assert item["kickoff_time"] == "2025-09-28T20:25Z"


class TestScoreboardEventToEventItemCoachInjuryDepthChart:
    """Coach/injuries/depth-chart are attached by ingest's _enrich_events
    (aws-lambdas/nfl/ingest/handler.py) before this function ever sees
    the event -- these fields are absent entirely on any event ingested
    before that shipped, or where a fetch failed, same sparse-optional
    convention weather_temperature already established."""

    def test_absent_when_not_present_on_raw_event(self):
        item = scoreboard_event_to_event_item(_scoreboard_event(), "nfl")

        for key in (
            "home_coach_id", "home_coach_name", "home_coach_experience", "home_coach_season_win_pct",
            "away_coach_id", "away_coach_name", "away_coach_experience", "away_coach_season_win_pct",
            "home_injuries", "away_injuries", "home_depth_chart", "away_depth_chart",
        ):
            assert key not in item

    def test_coach_flattened_into_top_level_attributes(self):
        raw = _scoreboard_event(
            home_coach={"coach_id": "1", "coach_name": "Andy Reid", "experience": 27, "season_win_pct": 0.7},
            away_coach={"coach_id": "2", "coach_name": "Jim Harbaugh", "experience": 1, "season_win_pct": 0.4},
        )

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["home_coach_id"] == "1"
        assert item["home_coach_name"] == "Andy Reid"
        assert item["home_coach_experience"] == 27
        assert item["home_coach_season_win_pct"] == 0.7
        assert item["away_coach_id"] == "2"
        assert item["away_coach_name"] == "Jim Harbaugh"

    def test_empty_injuries_list_is_kept_not_treated_as_absent(self):
        # An empty list is real signal ("checked, nobody's hurt"), not
        # the same as "never checked" -- must survive as [], not be
        # dropped the way a None value would be.
        raw = _scoreboard_event(home_injuries=[], away_injuries=[{"entity_id": "99", "status": "Out"}])

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["home_injuries"] == []
        assert item["away_injuries"] == [{"entity_id": "99", "status": "Out"}]

    def test_depth_chart_passed_through_as_is(self):
        depth_chart = {"qb": {"position": {"abbreviation": "QB"}, "athletes": [{"id": "1"}]}}
        raw = _scoreboard_event(home_depth_chart=depth_chart)

        item = scoreboard_event_to_event_item(raw, "nfl")

        assert item["home_depth_chart"] == depth_chart
        assert "away_depth_chart" not in item
