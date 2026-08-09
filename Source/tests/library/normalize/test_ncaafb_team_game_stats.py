"""
Unit tests for library.normalize.ncaafb.game_team_stats_to_team_game_stats
-- confirmed live against real 2025 CFBD /games/teams data (see
library/http/cfbd.py's get_game_team_stats docstring), including the
compound dash-separated values (thirdDownEff/fourthDownEff/
completionAttempts/totalPenaltiesYards) that verification pass surfaced.
"""
from library.normalize.ncaafb import game_team_stats_to_team_game_stats


def _team_box_score(game_id="401520281", event_date="2025-09-28", teams=None, home_id=None, away_id=None):
    box_score = {"id": game_id, "event_date": event_date, "teams": teams or []}
    if home_id is not None:
        box_score["home_id"] = home_id
    if away_id is not None:
        box_score["away_id"] = away_id
    return box_score


class TestGameTeamStatsToTeamGameStats:
    def test_returns_one_item_per_team(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "points": 30, "stats": [{"category": "turnovers", "stat": "1"}]},
            {"teamId": 52, "team": "Alabama", "homeAway": "away", "points": 24, "stats": [{"category": "turnovers", "stat": "2"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert len(items) == 2
        assert {item["team_id"] for item in items} == {"2", "52"}

    def test_team_id_resolved_from_teamid_field_directly(self):
        # Unlike get_game_player_stats, CFBD's /games/teams DOES carry a
        # numeric teamId on each team block -- no homeAway/injected-id
        # join needed for the common case.
        box_score = _team_box_score(teams=[{"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": []}])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["team_id"] == "2"
        assert items[0]["team_key"] == "TEAM#2"

    def test_falls_back_to_home_away_join_when_teamid_is_absent(self):
        box_score = _team_box_score(home_id="2", away_id="52", teams=[
            {"team": "Georgia", "homeAway": "home", "stats": []},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["team_id"] == "2"

    def test_event_key_and_date_are_set(self):
        box_score = _team_box_score(game_id="401520281", event_date="2025-09-28", teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": []},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["event_key"] == "SPORT#NCAAFB#EVENT#401520281"
        assert items[0]["event_date"] == "2025-09-28"
        assert items[0]["sport"] == "ncaafb"

    def test_category_name_is_snake_cased_into_the_stat_line(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": [{"category": "totalYards", "stat": "412"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["stat_line"] == {"total_yards": 412}

    def test_possession_time_is_parsed_to_seconds(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": [{"category": "possessionTime", "stat": "32:10"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["stat_line"] == {"possession_time_seconds": 1930}

    def test_third_down_eff_is_split_into_conversions_and_attempts(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": [{"category": "thirdDownEff", "stat": "7-10"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["stat_line"] == {"third_down_conversions": 7, "third_down_attempts": 10}

    def test_fourth_down_eff_is_split(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": [{"category": "fourthDownEff", "stat": "0-0"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["stat_line"] == {"fourth_down_conversions": 0, "fourth_down_attempts": 0}

    def test_completion_attempts_is_split(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": [{"category": "completionAttempts", "stat": "13-21"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["stat_line"] == {"completions": 13, "pass_attempts": 21}

    def test_total_penalties_yards_is_split(self):
        box_score = _team_box_score(teams=[
            {"teamId": 2, "team": "Georgia", "homeAway": "home", "stats": [{"category": "totalPenaltiesYards", "stat": "4-50"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items[0]["stat_line"] == {"penalties": 4, "penalty_yards": 50}

    def test_team_block_with_no_teamid_and_unresolvable_home_away_is_skipped(self):
        box_score = _team_box_score(teams=[
            {"team": "Neutral", "homeAway": "unknown", "stats": [{"category": "turnovers", "stat": "1"}]},
        ])

        items = game_team_stats_to_team_game_stats(box_score, "ncaafb")

        assert items == []
