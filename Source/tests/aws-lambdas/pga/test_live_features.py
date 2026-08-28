"""
Unit tests for aws-lambdas/pga/predict/live_features.py -- the highest-
risk new logic in the PGA serving Lambda pair (re-deriving each golfer's
own rolling history from CURRENT DynamoDB state instead of a training-
time Parquet snapshot).
"""
from unittest.mock import MagicMock

import pytest

import live_features


def _result(finish_position=None, score_to_par=None, earnings=0.0, status="finished", rounds=None):
    return {
        "finish_position": finish_position, "score_to_par": score_to_par, "earnings": earnings,
        "status": status, "rounds": rounds or [],
    }


def _round(round_number, score_to_par=None, total_strokes=None):
    return {"round": round_number, "score_to_par": score_to_par, "total_strokes": total_strokes}


def _field_event(event_id, event_date, participants, course_id=None, cut_score=None, purse=10000000):
    return {
        "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_type": "field",
        "event_date": event_date, "purse": purse, "is_major": False, "course_id": course_id,
        "cut_score": cut_score, "cut_count": 71 if cut_score is not None else 0,
        "participants": participants,
    }


def _participant(entity_id, **result_kwargs):
    return {"entity_id": entity_id, "result": _result(**result_kwargs)}


def _match_event(event_id, event_date, parent_event_id, home, away, match_format="Foursomes"):
    return {
        "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_type": "match_play",
        "event_date": event_date, "parent_event_id": parent_event_id, "match_format": match_format,
        "participants": [home, away],
    }


def _cup_event(event_id, event_date, home_id="1", away_id="3"):
    return {
        "event_key": f"SPORT#PGA#EVENT#{event_id}", "event_id": event_id, "event_type": "cup",
        "event_date": event_date, "tournament_name": "Presidents Cup",
        "participants": [
            {"entity_id": home_id, "role": "home", "result": {"points": 17.5, "won": True, "halved": False}},
            {"entity_id": away_id, "role": "away", "result": {"points": 12.5, "won": False, "halved": False}},
        ],
    }


def _storage(target_event, history_events):
    """Mocks get_event(target_event's own key) and get_all_events(sport)
    -> history_events (most-recent-first, matching the real GSI's default
    scan_index_forward=False), plus a real (unmocked) get_team_events so
    its own entity_id-filtering logic is actually exercised, not stubbed
    away."""
    storage = MagicMock()
    storage.get_event.return_value = target_event
    storage.get_all_events.return_value = history_events

    def _get_team_events(sport, entity_id, before_date=None, limit=None, events=None):
        all_events = events if events is not None else history_events
        team_events = [
            e for e in all_events
            if any(p.get("entity_id") == entity_id for p in e.get("participants", []))
            and (before_date is None or e.get("event_date", "") < before_date)
        ]
        return team_events[:limit] if limit is not None else team_events

    storage.get_team_events.side_effect = _get_team_events
    return storage


class TestApplicableRounds:
    def test_no_rounds_played_yet_means_every_round_is_due(self):
        assert live_features.applicable_rounds(_participant("1")) == [1, 2, 3, 4]

    def test_two_rounds_played_means_rounds_3_and_4_are_due(self):
        p = _participant("1", rounds=[_round(1, -2), _round(2, 0)])
        assert live_features.applicable_rounds(p) == [3, 4]

    def test_all_four_rounds_played_means_nothing_is_due(self):
        p = _participant("1", rounds=[_round(1), _round(2), _round(3), _round(4)])
        assert live_features.applicable_rounds(p) == []

    def test_cut_golfer_has_nothing_due_even_with_rounds_remaining(self):
        p = _participant("1", status="cut", rounds=[_round(1), _round(2)])
        assert live_features.applicable_rounds(p) == []

    def test_withdrawn_golfer_has_nothing_due(self):
        p = _participant("1", status="withdrawn", rounds=[_round(1)])
        assert live_features.applicable_rounds(p) == []

    def test_made_cut_did_not_finish_has_nothing_due(self):
        """The trickiest case -- MDF nominally "made the cut" (unlike an
        outright cut golfer) but has withdrawn mid-tournament, so still
        has nothing left to project."""
        p = _participant("1", status="made_cut_did_not_finish", rounds=[_round(1), _round(2), _round(3)])
        assert live_features.applicable_rounds(p) == []


class TestBuildLiveFieldFeatures:
    def test_raises_when_event_not_found(self):
        storage = _storage(None, [])
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_field_features(storage, "pga", "999")

    def test_raises_when_event_is_not_a_field_event(self):
        storage = _storage(_cup_event("999", "2026-08-20"), [])
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_field_features(storage, "pga", "999")

    def test_builds_one_golfer_row_per_participant(self):
        target = _field_event("999", "2026-08-20", [_participant("1"), _participant("2")])
        storage = _storage(target, [])

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert set(result["golfer_rows"]) == {"1", "2"}
        assert "cutline_row" in result

    def test_prior_results_only_come_from_strictly_earlier_field_events(self):
        past = _field_event("998", "2026-08-01", [_participant("1", finish_position=3, score_to_par=-10)])
        target = _field_event("999", "2026-08-20", [_participant("1")])
        storage = _storage(target, [target, past])  # get_all_events would include the target too in a real query

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert result["golfer_rows"]["1"]["golfer"]["avg_finish_position"] == 3.0

    def test_a_match_play_event_sharing_this_golfers_id_never_contaminates_stroke_play_history(self):
        """Real gap this guards: a WGC individual match_play event's own
        participant has this golfer's id doubling as entity_id (library/
        normalize/pga_matchplay.py) -- get_team_events' entity_id filter
        alone would include it."""
        wgc_match = {
            "event_key": "SPORT#PGA#EVENT#M1", "event_id": "M1", "event_type": "match_play",
            "event_date": "2026-08-10", "participants": [
                {"entity_id": "1", "role": "home", "golfer_entity_ids": ["1"], "result": {"won": True, "halved": False}},
                {"entity_id": "2", "role": "away", "golfer_entity_ids": ["2"], "result": {"won": False, "halved": False}},
            ],
        }
        past_field = _field_event("998", "2026-08-01", [_participant("1", finish_position=5, score_to_par=-6)])
        target = _field_event("999", "2026-08-20", [_participant("1")])
        storage = _storage(target, [target, wgc_match, past_field])

        result = live_features.build_live_field_features(storage, "pga", "999")

        # If the match_play event leaked in, avg_finish_position would be
        # None (a match result dict has no finish_position key at all) or
        # the averages would be computed over the wrong row count.
        assert result["golfer_rows"]["1"]["golfer"]["avg_finish_position"] == 5.0
        assert result["golfer_rows"]["1"]["golfer"]["events_played"] == 1

    def test_course_history_never_leaks_a_different_courses_results(self):
        other_course = _field_event("997", "2026-07-01", [_participant("1", score_to_par=-15)], course_id="C2")
        same_course = _field_event("998", "2026-08-01", [_participant("1", score_to_par=-8)], course_id="C1")
        target = _field_event("999", "2026-08-20", [_participant("1")], course_id="C1")
        storage = _storage(target, [target, other_course, same_course])

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert result["golfer_rows"]["1"]["golfer"]["course_avg_score_to_par"] == -8.0
        assert result["golfer_rows"]["1"]["golfer"]["course_events_played"] == 1

    def test_no_course_id_on_the_target_event_means_no_course_history(self):
        target = _field_event("999", "2026-08-20", [_participant("1")], course_id=None)
        storage = _storage(target, [target])

        result = live_features.build_live_field_features(storage, "pga", "999")

        # rolling_golfer_averages([]) reports events_played as a real 0,
        # not None -- only the averages themselves (avg_score_to_par etc.)
        # go to None when there's nothing to average.
        assert result["golfer_rows"]["1"]["golfer"]["course_events_played"] == 0
        assert result["golfer_rows"]["1"]["golfer"]["course_avg_score_to_par"] is None

    def test_round_rows_built_for_every_remaining_round_not_just_the_next_one(self):
        target = _field_event("999", "2026-08-20", [_participant("1", rounds=[_round(1, -2)])])
        storage = _storage(target, [target])

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert set(result["golfer_rows"]["1"]["rounds"]) == {2, 3, 4}
        assert result["golfer_rows"]["1"]["rounds"][2]["round_number"] == 2
        assert result["golfer_rows"]["1"]["rounds"][3]["round_number"] == 3
        assert result["golfer_rows"]["1"]["rounds"][4]["round_number"] == 4

    def test_eliminated_golfer_gets_no_round_rows_at_all(self):
        target = _field_event("999", "2026-08-20", [_participant("1", status="cut", rounds=[_round(1), _round(2)])])
        storage = _storage(target, [target])

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert result["golfer_rows"]["1"]["rounds"] == {}

    def test_golfer_row_reflects_this_tournaments_own_rounds_played_so_far(self):
        # The real bug this fixes: PROJ/top-10%/top-5% used to stay
        # completely unchanged after round 1 finished, because nothing
        # fed the model any signal about the CURRENT, in-progress
        # tournament -- only rolling averages over OTHER, past ones.
        target = _field_event("999", "2026-08-20", [_participant("1", rounds=[_round(1, -3), _round(2, 1)])])
        storage = _storage(target, [target])

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert result["golfer_rows"]["1"]["golfer"]["rounds_completed_this_week"] == 2
        assert result["golfer_rows"]["1"]["golfer"]["score_to_par_this_week_so_far"] == -2

    def test_golfer_row_reports_the_pre_tournament_state_when_nothing_has_been_played_yet(self):
        target = _field_event("999", "2026-08-20", [_participant("1")])
        storage = _storage(target, [target])

        result = live_features.build_live_field_features(storage, "pga", "999")

        assert result["golfer_rows"]["1"]["golfer"]["rounds_completed_this_week"] == 0
        assert result["golfer_rows"]["1"]["golfer"]["score_to_par_this_week_so_far"] is None

    def test_prior_same_round_results_only_pull_matching_round_numbers(self):
        past = _field_event("998", "2026-08-01", [_participant(
            "1", rounds=[_round(1, -2, 68.0), _round(2, 1, 71.0), _round(3, -1, 69.0)],
        )])
        target = _field_event("999", "2026-08-20", [_participant("1", rounds=[_round(1, 0), _round(2, -1)])])
        storage = _storage(target, [target, past])

        result = live_features.build_live_field_features(storage, "pga", "999")

        # Round 3 is due (2 played); its same_round history must only
        # ever draw from this golfer's own past round-3 entries, never
        # round 1 or round 2's.
        assert result["golfer_rows"]["1"]["rounds"][3]["same_round_avg_score_to_par"] == -1.0
        assert result["golfer_rows"]["1"]["rounds"][3]["same_round_rounds_played"] == 1


class TestBuildLiveMatchFeatures:
    def test_raises_when_event_not_found(self):
        storage = _storage(None, [])
        with pytest.raises(live_features.EventNotFoundError):
            live_features.build_live_match_features(storage, "pga", "999")

    def test_raises_when_not_a_match_play_event(self):
        storage = _storage(_field_event("999", "2026-08-20", []), [])
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_match_features(storage, "pga", "999")

    def test_raises_when_missing_home_or_away(self):
        malformed = {
            "event_key": "SPORT#PGA#EVENT#999", "event_id": "999", "event_type": "match_play",
            "event_date": "2026-09-25", "participants": [{"entity_id": "1", "role": "home", "golfer_entity_ids": ["1"]}],
        }
        storage = _storage(malformed, [])
        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_match_features(storage, "pga", "999")

    def test_builds_a_feature_row_from_each_sides_own_stroke_play_history(self):
        home = {"entity_id": "1", "role": "home", "golfer_entity_ids": ["1085"], "result": {"won": False, "halved": False}}
        away = {"entity_id": "3", "role": "away", "golfer_entity_ids": ["2001"], "result": {"won": False, "halved": False}}
        target = _match_event("999", "2026-09-25", "401465497", home, away)
        past = _field_event("998", "2026-08-01", [_participant("1085", score_to_par=-5), _participant("2001", score_to_par=-2)])
        storage = _storage(target, [target, past])

        result = live_features.build_live_match_features(storage, "pga", "999")

        assert result["features"]["home_avg_score_to_par"] == -5.0
        assert result["features"]["away_avg_score_to_par"] == -2.0


class TestBuildLiveCupFeatures:
    def test_raises_when_no_match_sessions_exist_yet(self):
        """Locked-in project decision, 2026-08-27: a Cup's roster can't
        be resolved (and so its outcome can't be predicted) before at
        least one match_play session exists."""
        target = _cup_event("999", "2026-09-22")
        storage = _storage(target, [target])

        with pytest.raises(live_features.MalformedEventError):
            live_features.build_live_cup_features(storage, "pga", "999")

    def test_builds_a_feature_row_from_the_full_roster_across_every_played_session(self):
        target = _cup_event("999", "2026-09-22", home_id="1", away_id="3")
        thursday = _match_event(
            "999-match-1", "2026-09-22", "999",
            {"entity_id": "1", "role": "home", "golfer_entity_ids": ["1085", "1086"]},
            {"entity_id": "3", "role": "away", "golfer_entity_ids": ["2001"]},
        )
        friday = _match_event(
            "999-match-2", "2026-09-23", "999",
            {"entity_id": "1", "role": "home", "golfer_entity_ids": ["1086", "1087"]},  # 1087 only appears here
            {"entity_id": "3", "role": "away", "golfer_entity_ids": ["2002"]},
        )
        past = _field_event("998", "2026-08-01", [
            _participant("1085", score_to_par=-4), _participant("1086", score_to_par=-6),
            _participant("1087", score_to_par=-2), _participant("2001", score_to_par=0), _participant("2002", score_to_par=-1),
        ])
        storage = _storage(target, [target, thursday, friday, past])

        result = live_features.build_live_cup_features(storage, "pga", "999")

        # home roster is the UNION across both sessions (1085, 1086, 1087)
        assert result["features"]["home_avg_score_to_par"] == pytest.approx((-4 + -6 + -2) / 3)
        assert result["features"]["away_avg_score_to_par"] == pytest.approx((0 + -1) / 2)
