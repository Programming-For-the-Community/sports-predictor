"""
Unit tests for library.normalize.espn.boxscore_to_team_game_stats --
flat (non-category-grouped) stat-name snake-casing and compound-value
splitting (e.g. "3-9" -> conversions/attempts, "23:45" -> seconds). Hand-
built synthetic payloads. Split out of what used to be one large
test_espn.py -- see test_espn_player_game_stats.py's own history note.
"""
from library.normalize.espn import boxscore_to_team_game_stats

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
