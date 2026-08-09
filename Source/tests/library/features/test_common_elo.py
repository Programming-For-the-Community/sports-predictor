"""
Unit tests for library.features.common's Elo rating system:
compute_elo_ratings (margin-of-victory-scaled K-factor updates, walked
chronologically regardless of input order), season carryover (regression
toward the starting rating at a season boundary), and expected_score (the
underlying win-probability formula both consume). No AWS involved. Split
out of what used to be one large test_common.py -- see
test_common_rolling_stats.py and test_common_ranking_injury.py for this
file's siblings, one per concern.
"""
import pytest

from library.features.common import _mov_multiplier, compute_elo_ratings, expected_score


def _event(event_key, event_date, home_id, away_id, home_score=None, away_score=None, season=None):
    home_result = {"score": home_score, "won": home_score is not None and home_score > away_score}
    away_result = {"score": away_score, "won": away_score is not None and away_score > home_score}
    event = {
        "event_key": event_key,
        "event_date": event_date,
        "participants": [
            {"entity_id": home_id, "role": "home", "result": home_result},
            {"entity_id": away_id, "role": "away", "result": away_result},
        ],
    }
    if season is not None:
        event["season"] = season
    return event


class TestComputeEloRatings:
    def test_first_meeting_starts_both_teams_at_starting_rating(self):
        events = [_event("E1", "2025-09-07", "KC", "LAC", 27, 20)]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500)

        assert ratings["E1"]["home_pre_rating"] == 1500
        assert ratings["E1"]["away_pre_rating"] == 1500

    def test_home_win_updates_ratings_per_elo_formula(self):
        # expected_home = 1 / (1 + 10^((1500 - (1500+55)) / 400)) ~= 0.5785
        # mov_multiplier for a 7-point win with a 55-point winner_elo_diff
        # (the home team's own pre-game edge, home advantage included)
        # ~= 2.0287 -- see _mov_multiplier. E2's pre-game rating reflects
        # E1's MOV-scaled update.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 27, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, k_factor=20, home_advantage=55, starting_rating=1500)

        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1517.10, abs=0.05)
        assert ratings["E2"]["away_pre_rating"] == pytest.approx(1482.90, abs=0.05)

    def test_bigger_margin_moves_ratings_more(self):
        # compute_elo_ratings only exposes PRE-game ratings, so the
        # movement from E1 is read via a follow-up event's pre-game rating.
        followup = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)
        close_win = [_event("E1", "2025-09-07", "KC", "LAC", 24, 20), followup]
        blowout = [_event("E1", "2025-09-07", "KC", "LAC", 45, 3), followup]

        close_ratings, _ = compute_elo_ratings(close_win, starting_rating=1500)
        blowout_ratings, _ = compute_elo_ratings(blowout, starting_rating=1500)

        close_gain = close_ratings["E2"]["home_pre_rating"] - 1500
        blowout_gain = blowout_ratings["E2"]["home_pre_rating"] - 1500
        assert blowout_gain > close_gain > 0

    def test_underdog_blowout_moves_ratings_more_than_favorite_blowout(self):
        # Tested directly against the multiplier -- compute_elo_ratings
        # has no way to seed teams at a non-starting rating.
        favorite_mult = _mov_multiplier(28, winner_elo_diff=600, base=2.2, divisor=0.001)
        underdog_mult = _mov_multiplier(28, winner_elo_diff=-600, base=2.2, divisor=0.001)

        assert underdog_mult > favorite_mult

    def test_tie_applies_no_mov_scaling(self):
        # A tie has no winner to measure a margin from -- the multiplier
        # must be a flat 1.0, not the ln(0+1)=0 the formula would
        # otherwise produce.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 20, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500, home_advantage=10)

        # With home_advantage=10 (unequal expected outcome) and a tied
        # result, ratings should still move by exactly k_factor * (0.5 -
        # expected) -- i.e. multiplier == 1.0, not 0.
        expected_home = 1 / (1 + 10 ** ((1500 - (1500 + 10)) / 400))
        expected_move = 20.0 * (0.5 - expected_home)
        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1500 + expected_move, abs=0.01)

    def test_away_win_decreases_home_rating(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 17, 24),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500)

        assert ratings["E2"]["home_pre_rating"] < 1500

    def test_processes_chronologically_regardless_of_input_order(self):
        earlier = _event("E1", "2025-09-07", "KC", "LAC", 27, 20)
        later = _event("E2", "2025-09-14", "KC", "LAC", 20, 17)

        forward, _ = compute_elo_ratings([earlier, later], starting_rating=1500)
        reversed_input, _ = compute_elo_ratings([later, earlier], starting_rating=1500)

        assert forward["E2"]["home_pre_rating"] == reversed_input["E2"]["home_pre_rating"]

    def test_tie_moves_both_ratings_toward_each_other_evenly_when_equal(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 20, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500, home_advantage=0)

        # Equal pre-game ratings, no home advantage, tied result -> no change.
        assert ratings["E2"]["home_pre_rating"] == pytest.approx(1500, abs=0.01)
        assert ratings["E2"]["away_pre_rating"] == pytest.approx(1500, abs=0.01)

    def test_event_missing_home_or_away_role_is_skipped_without_error(self):
        malformed = {
            "event_key": "BAD",
            "event_date": "2025-09-07",
            "participants": [{"entity_id": "KC", "role": "home", "result": {"score": 10}}],
        }
        events = [malformed, _event("E1", "2025-09-14", "KC", "LAC", 27, 20)]

        ratings, _ = compute_elo_ratings(events, starting_rating=1500)

        assert "BAD" not in ratings
        assert ratings["E1"]["home_pre_rating"] == 1500

    def test_event_missing_score_records_pre_rating_but_skips_update(self):
        scheduled = _event("E1", "2025-09-07", "KC", "LAC")  # no scores
        played = _event("E2", "2025-09-14", "KC", "LAC", 27, 20)

        ratings, _ = compute_elo_ratings([scheduled, played], starting_rating=1500)

        assert ratings["E1"]["home_pre_rating"] == 1500
        # Unaffected by the scoreless event -- still starting_rating.
        assert ratings["E2"]["home_pre_rating"] == 1500

    def test_current_ratings_reflect_every_processed_event_not_just_pre_game(self):
        # current_ratings (the second return value) is each team's rating
        # AFTER all events passed in -- e.g. for a live inference request
        # about a not-yet-played game, which has no pre_game_ratings entry
        # of its own to look up.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 27, 20),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        _, current_ratings = compute_elo_ratings(events, starting_rating=1500)

        assert current_ratings["KC"] > 1500  # won both games
        assert current_ratings["LAC"] < 1500  # lost both games

    def test_current_ratings_for_a_team_with_no_events_is_absent(self):
        events = [_event("E1", "2025-09-07", "KC", "LAC", 27, 20)]

        _, current_ratings = compute_elo_ratings(events, starting_rating=1500)

        assert "DEN" not in current_ratings


class TestSeasonCarryover:
    def test_no_season_field_never_triggers_regression(self):
        # Every existing caller that already scopes its own events to one
        # season passes events with no `season` key at all -- confirms
        # that path is completely unaffected by this feature.
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 45, 3),
            _event("E2", "2025-09-14", "KC", "LAC", 20, 17),
        ]

        _, current_ratings = compute_elo_ratings(events, starting_rating=1500)

        # KC won both games -- if a phantom regression fired between them,
        # E2's pre-game rating (and so the final current_ratings) would sit
        # closer to 1500 than an unregressed two-game win streak produces.
        assert current_ratings["KC"] > 1517  # > the single-blowout-win figure from other tests

    def test_a_new_seasons_first_game_uses_the_regressed_prior_rating(self):
        blowout = _event("E1", "2025-09-07", "KC", "LAC", 45, 3, season=2025)
        first_2026_game = _event("E2", "2026-09-06", "KC", "DEN", season=2026)  # no score -- just needs a pre-rating

        pre_game_ratings, _ = compute_elo_ratings([blowout, first_2026_game], starting_rating=1500)

        # KC's rating after the 2025 blowout (unregressed) is ~1531 -- see
        # test_bigger_margin_moves_ratings_more's own figures for the same
        # inputs. Regressed 2/3 of the way back per DEFAULT_SEASON_CARRYOVER:
        # 1500 + (2/3)*(1531-1500) ~= 1520.7 -- above 1500 (some carryover)
        # but well below the full unregressed 1531 (not a full carryover).
        kc_2026_pre_rating = pre_game_ratings["E2"]["home_pre_rating"]
        assert 1500 < kc_2026_pre_rating < 1531

    def test_as_of_season_regresses_even_with_no_game_played_yet_this_season(self):
        # The realistic pre-season case: every event is still from LAST
        # season, nothing in `events` itself ever reaches a 2026 game to
        # trigger the in-loop transition -- as_of_season is what a caller
        # building a season-projection for 2026 passes to get a regressed
        # rating anyway, instead of the raw, unregressed final 2025 one.
        events = [_event("E1", "2025-09-07", "KC", "LAC", 45, 3, season=2025)]

        _, unregressed = compute_elo_ratings(events, starting_rating=1500)
        _, regressed = compute_elo_ratings(events, starting_rating=1500, as_of_season=2026)

        assert regressed["KC"] < unregressed["KC"]
        assert 1500 < regressed["KC"] < unregressed["KC"]

    def test_as_of_season_matching_the_last_event_is_a_no_op(self):
        events = [_event("E1", "2025-09-07", "KC", "LAC", 45, 3, season=2025)]

        _, without = compute_elo_ratings(events, starting_rating=1500)
        _, with_matching = compute_elo_ratings(events, starting_rating=1500, as_of_season=2025)

        assert without == with_matching

    def test_season_carryover_zero_is_a_full_reset(self):
        events = [
            _event("E1", "2025-09-07", "KC", "LAC", 45, 3, season=2025),
            _event("E2", "2026-09-06", "KC", "DEN", season=2026),
        ]

        pre_game_ratings, _ = compute_elo_ratings(events, starting_rating=1500, season_carryover=0.0)

        assert pre_game_ratings["E2"]["home_pre_rating"] == 1500


class TestExpectedScore:
    def test_equal_ratings_no_advantage_is_a_coin_flip(self):
        assert expected_score(1500, 1500) == pytest.approx(0.5)

    def test_higher_rating_favored(self):
        assert expected_score(1600, 1500) > 0.5

    def test_home_advantage_shifts_probability_toward_the_home_side(self):
        even = expected_score(1500, 1500, rating_advantage=0)
        with_advantage = expected_score(1500, 1500, rating_advantage=55)

        assert with_advantage > even

    def test_symmetric_with_its_opponent(self):
        home = expected_score(1500, 1600, rating_advantage=55)
        away = expected_score(1600, 1500, rating_advantage=-55)

        assert home == pytest.approx(1 - away)
