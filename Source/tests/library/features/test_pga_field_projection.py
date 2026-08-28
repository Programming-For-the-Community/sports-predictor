"""
Unit tests for library.features.pga_field_projection -- course_id-primary
prior-season matching and the skip/withdrawal distinction it drives.
"""
from library.features.pga_field_projection import (
    match_prior_season_event,
    project_remaining_field,
    remaining_event_has_cut,
)


def _event(course_id=None, tournament_name=None, event_date="2025-06-01", participants=None, cut_count=0):
    return {
        "course_id": course_id, "tournament_name": tournament_name, "event_date": event_date,
        "participants": participants or [], "cut_count": cut_count,
    }


def _participant(entity_id, status="finished"):
    return {"entity_id": entity_id, "result": {"status": status}}


class TestMatchPriorSeasonEvent:
    def test_matches_by_course_id_even_with_no_tournament_name_on_either_side(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="65", event_date="2025-08-01")
        assert match_prior_season_event(remaining, [prior]) is prior

    def test_no_course_id_on_the_remaining_event_returns_none(self):
        remaining = _event(course_id=None)
        prior = _event(course_id="65")
        assert match_prior_season_event(remaining, [prior]) is None

    def test_no_prior_event_at_this_course_returns_none(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="99")
        assert match_prior_season_event(remaining, [prior]) is None

    def test_multiple_candidates_at_the_same_course_prefer_a_real_tournament_name_match(self):
        remaining = _event(course_id="65", tournament_name="BMW Championship")
        wrong = _event(course_id="65", tournament_name="Some Other Event", event_date="2025-09-01")
        right = _event(course_id="65", tournament_name="BMW Championship", event_date="2025-08-01")
        assert match_prior_season_event(remaining, [wrong, right]) is right

    def test_multiple_candidates_with_no_name_signal_falls_back_to_most_recent(self):
        remaining = _event(course_id="65", tournament_name=None)
        older = _event(course_id="65", event_date="2024-08-01")
        newer = _event(course_id="65", event_date="2025-08-01")
        assert match_prior_season_event(remaining, [older, newer]) is newer


class TestProjectRemainingField:
    def test_a_tracked_golfer_missing_entirely_from_the_matched_prior_event_is_a_confirmed_skip(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="65", participants=[_participant("1")])
        result = project_remaining_field(remaining, [prior], tracked_roster=["1", "2"])
        assert result == ["1"]  # golfer "2" skipped this event last year

    def test_a_withdrawal_is_not_treated_as_a_skip(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="65", participants=[_participant("1", status="withdrawn")])
        result = project_remaining_field(remaining, [prior], tracked_roster=["1"])
        assert result == ["1"]  # had a real row, just withdrew -- not a confirmed skip

    def test_no_matched_prior_event_includes_every_tracked_golfer_by_default(self):
        remaining = _event(course_id="65")
        result = project_remaining_field(remaining, prior_season_events=[], tracked_roster=["1", "2", "3"])
        assert result == ["1", "2", "3"]

    def test_a_golfer_never_tracked_this_season_is_simply_never_considered(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="65", participants=[_participant("1")])
        result = project_remaining_field(remaining, [prior], tracked_roster=["1"])
        assert "99" not in result


class TestRemainingEventHasCut:
    def test_reads_real_cut_count_off_the_matched_prior_event(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="65", cut_count=71)
        assert remaining_event_has_cut(remaining, [prior]) is True

    def test_a_zero_cut_count_prior_instance_means_no_cut(self):
        remaining = _event(course_id="65")
        prior = _event(course_id="65", cut_count=0)
        assert remaining_event_has_cut(remaining, [prior]) is False

    def test_no_matched_prior_event_defaults_to_true(self):
        remaining = _event(course_id="65")
        assert remaining_event_has_cut(remaining, []) is True
