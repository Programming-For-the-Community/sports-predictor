"""
Unit tests for library.normalize.ncaafb.game_player_stats_to_player_game_stats
-- category/type-name mapping into TARGET_STAT-matching field names
(Terraform/dynamodb-sport-registry.tf's ncaafb_player_prop_stats), team id
resolution via homeAway + the home_id/away_id ingest injects (see
aws-lambdas/ncaafb/ingest/handler.py's _annotate_box_scores), and a single
athlete's stats merging across categories. Hand-built synthetic payloads,
shaped like CFBD's confirmed-live /games/players response.
"""
from library.normalize.ncaafb import game_player_stats_to_player_game_stats


def _box_score(game_id="401520281", home_id="2", away_id="52", event_date="2025-09-28", teams=None):
    return {
        "id": game_id,
        "home_id": home_id,
        "away_id": away_id,
        "event_date": event_date,
        "teams": teams or [],
    }


def _team_block(home_away, categories):
    return {"team": "Georgia" if home_away == "home" else "Alabama", "homeAway": home_away, "categories": categories}


class TestGamePlayerStatsToPlayerGameStats:
    def test_resolves_team_id_from_home_away_not_school_name(self):
        box_score = _box_score(home_id="2", away_id="52", teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": "111", "name": "Carson Beck", "stat": "250"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["team_id"] == "2"

    def test_passing_yards_maps_to_target_stat_name(self):
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": "111", "name": "Carson Beck", "stat": "250"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["stat_line"] == {"passing_yards": 250}

    def test_passing_touchdowns_maps_to_target_stat_name(self):
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "TD", "athletes": [{"id": "111", "name": "Carson Beck", "stat": "3"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["stat_line"] == {"passing_touchdowns": 3}

    def test_slash_separated_type_name_maps_correctly(self):
        # Confirmed live: CFBD's real type name is "C/ATT" (slash), not
        # the dash-separated "C-ATT" this project's own Phase 1 notes
        # assumed -- a real gap this test locks in against regressing.
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "C/ATT", "athletes": [{"id": "111", "name": "Carson Beck", "stat": "20/28"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["stat_line"] == {"passing_completions_attempts": "20/28"}

    def test_unmapped_slash_separated_type_name_falls_back_to_lowercase_with_underscore(self):
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "kicking", "types": [
                {"name": "FG/ATT", "athletes": [{"id": "111", "name": "Kicker", "stat": "2/3"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["stat_line"] == {"kicking_fg_att": "2/3"}

    def test_defensive_sacks_maps_to_target_stat_name_not_passing_sacks(self):
        # Mirrors the exact ambiguity NFL/ESPN hit -- confirmed in Phase 1
        # that CFBD's "passing" category has no SACKS type at all, only
        # "defensive" does, so there's no collision to guard against here,
        # but the target field name must still land as defensive_sacks.
        box_score = _box_score(teams=[
            _team_block("away", [{"name": "defensive", "types": [
                {"name": "SACKS", "athletes": [{"id": "222", "name": "Will Anderson", "stat": "2"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["stat_line"] == {"defensive_sacks": 2}
        assert stats_items[0]["team_id"] == box_score["away_id"]

    def test_unmapped_type_name_falls_back_to_snake_case(self):
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "rushing", "types": [
                {"name": "CAR", "athletes": [{"id": "111", "name": "Trevor Etienne", "stat": "18"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["stat_line"] == {"rushing_car": 18}

    def test_single_athlete_stats_merge_across_categories(self):
        box_score = _box_score(teams=[
            _team_block("home", [
                {"name": "passing", "types": [{"name": "YDS", "athletes": [{"id": "111", "name": "QB1", "stat": "250"}]}]},
                {"name": "rushing", "types": [{"name": "YDS", "athletes": [{"id": "111", "name": "QB1", "stat": "40"}]}]},
            ]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert len(stats_items) == 1
        assert stats_items[0]["stat_line"] == {"passing_yards": 250, "rushing_yards": 40}

    def test_event_key_and_player_key_are_sport_scoped(self):
        box_score = _box_score(game_id="401520281", teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": "111", "name": "QB1", "stat": "250"}]},
            ]}]),
        ])

        stats_items, _ = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items[0]["event_key"] == "SPORT#NCAAFB#EVENT#401520281"
        assert stats_items[0]["player_key"] == "SPORT#NCAAFB#PLAYER#111"

    def test_player_entity_position_is_always_none(self):
        # CFBD's box-score athletes carry no position field, unlike
        # ESPN's -- position would come from the roster feed instead,
        # which isn't wired into ingest yet.
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": "111", "name": "QB1", "stat": "250"}]},
            ]}]),
        ])

        _, player_entities = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert player_entities[0]["metadata"]["position"] is None

    def test_athlete_with_no_id_is_skipped(self):
        box_score = _box_score(teams=[
            _team_block("home", [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": None, "name": "TEAM", "stat": "250"}]},
            ]}]),
        ])

        stats_items, player_entities = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items == []
        assert player_entities == []

    def test_team_block_with_unresolvable_home_away_is_skipped(self):
        box_score = _box_score(home_id="2", away_id="52", teams=[
            {"team": "Neutral", "homeAway": "unknown", "categories": [{"name": "passing", "types": [
                {"name": "YDS", "athletes": [{"id": "111", "name": "QB1", "stat": "250"}]},
            ]}]},
        ])

        stats_items, player_entities = game_player_stats_to_player_game_stats(box_score, "ncaafb")

        assert stats_items == []
        assert player_entities == []
