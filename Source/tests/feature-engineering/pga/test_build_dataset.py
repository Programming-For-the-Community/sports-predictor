"""
Unit tests for the PGA feature-engineering entrypoint's orchestration
logic -- the chronological golfer-history walk and Parquet assembly, plus
the round-level/cut-line dataset builders. The actual feature math is
tested in tests/library/features/test_pga.py; the season-stats raw-
snapshot loader/resolver itself is tested in tests/library/storage/
test_pga_season_stats.py (extracted there 2026-08-27, see library/
storage/pga_season_stats.py) -- TestBuildGolferDatasetSeasonStats below
only covers build_golfer_dataset's own wiring to it. FeatureStorage/
S3Manager are mocked here so these tests only cover build_dataset.py's
own wiring.
"""
import io
from unittest.mock import MagicMock

import pandas as pd
import pytest

import build_dataset


def _event(event_key, event_date, participants, course_id=None, cut_score=None, cut_count=None, rounds_by_participant=None, event_type="field"):
    for participant in participants:
        rounds = (rounds_by_participant or {}).get(participant["entity_id"], [])
        participant["result"]["rounds"] = rounds
    return {
        "event_key": event_key, "event_date": event_date, "event_type": event_type, "purse": 10000000, "is_major": False,
        "course_id": course_id, "cut_score": cut_score, "cut_count": cut_count, "participants": participants,
    }


def _participant(entity_id, finish_position=None, score_to_par=None, earnings=0.0):
    return {
        "entity_id": entity_id,
        "result": {"finish_position": finish_position, "score_to_par": score_to_par, "earnings": earnings, "rounds": []},
    }


def _round(round_number, score_to_par=None):
    return {"round": round_number, "score_to_par": score_to_par, "total_strokes": None}


class TestWriteParquet:
    def test_empty_rows_returns_empty_bytes(self):
        assert build_dataset._write_parquet([]) == b""

    def test_writes_a_readable_parquet_frame(self):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

        df = pd.read_parquet(io.BytesIO(build_dataset._write_parquet(rows)))

        assert list(df.columns) == ["a", "b"]
        assert df.iloc[1]["a"] == 3


class TestBuildGolferDataset:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_builds_one_row_per_participant_per_event(self):
        events = [_event("E1", "2026-06-01", [_participant("1", finish_position=1), _participant("2", finish_position=2)])]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        assert {(r["event_key"], r["entity_id"]) for r in rows} == {("E1", "1"), ("E1", "2")}
        storage.get_all_events.assert_called_once_with(build_dataset.SPORT)

    def test_second_event_sees_only_the_first_events_result_as_history(self):
        events = [
            _event("E1", "2026-06-01", [_participant("1", finish_position=1, score_to_par=-15, earnings=1000000)]),
            _event("E2", "2026-06-08", [_participant("1", finish_position=5)]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        e1_row = next(r for r in rows if r["event_key"] == "E1")
        e2_row = next(r for r in rows if r["event_key"] == "E2")
        assert e1_row["events_played"] == 0  # no prior tournaments yet
        assert e2_row["events_played"] == 1
        assert e2_row["avg_finish_position"] == 1

    def test_a_golfers_own_result_this_event_does_not_leak_into_their_own_row(self):
        # Two golfers in the SAME event -- neither should see the other's
        # (or their own) same-event result as "history".
        events = [_event("E1", "2026-06-01", [_participant("1", finish_position=1), _participant("2", finish_position=50)])]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        for row in rows:
            assert row["events_played"] == 0

    def test_history_stays_capped_at_window_across_many_events(self):
        events = [
            _event(f"E{i}", f"2026-0{i}-01", [_participant("1", finish_position=i)])
            for i in range(1, 8)
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=3)

        last_row = next(r for r in rows if r["event_key"] == "E7")
        assert last_row["events_played"] == 3

    def test_field_size_reflects_this_events_own_participant_count(self):
        participants = [_participant(str(i)) for i in range(1, 6)]
        events = [_event("E1", "2026-06-01", participants)]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        assert all(row["field_size"] == 5 for row in rows)


class TestCourseFitHistory:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_second_appearance_at_the_same_course_sees_course_specific_history(self):
        events = [
            _event("E1", "2025-08-01", [_participant("1", finish_position=1, score_to_par=-15, earnings=1000000)], course_id="65"),
            _event("E2", "2026-08-01", [_participant("1", finish_position=5)], course_id="65"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        e2_row = next(r for r in rows if r["event_key"] == "E2")
        assert e2_row["course_events_played"] == 1
        assert e2_row["course_avg_finish_position"] == 1

    def test_a_different_course_does_not_contribute_to_course_specific_history(self):
        events = [
            _event("E1", "2025-08-01", [_participant("1", finish_position=1)], course_id="65"),
            _event("E2", "2026-08-01", [_participant("1", finish_position=5)], course_id="99"),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        e2_row = next(r for r in rows if r["event_key"] == "E2")
        assert e2_row["course_events_played"] == 0  # different course -- E1 doesn't count
        # ... but the golfer's own OVERALL history still saw it.
        assert e2_row["events_played"] == 1

    def test_missing_course_id_gets_no_course_history_but_still_gets_overall_history(self):
        events = [
            _event("E1", "2025-08-01", [_participant("1", finish_position=1)], course_id=None),
            _event("E2", "2026-08-01", [_participant("1", finish_position=5)], course_id=None),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        e2_row = next(r for r in rows if r["event_key"] == "E2")
        assert e2_row["course_events_played"] == 0
        assert e2_row["events_played"] == 1

    def test_course_history_stays_capped_at_course_window_across_many_appearances(self):
        events = [
            _event(f"E{i}", f"202{i}-08-01", [_participant("1", finish_position=i)], course_id="65")
            for i in range(1, 8)
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=10, course_window=3)

        last_row = next(r for r in rows if r["event_key"] == "E7")
        assert last_row["course_events_played"] == 3


class TestBuildGolferDatasetSeasonStats:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_season_stats_are_resolved_per_participant_per_event(self):
        events = [_event("E1", "2026-08-15", [_participant("1")])]
        storage = self._storage(events)
        snapshots = [{"as_of_date": "2026-08-01", "value_by_category_and_athlete": {"yardsPerDrive": {"1": 315.5}}}]

        rows = build_dataset.build_golfer_dataset(storage, window=5, season_stat_snapshots=snapshots)

        assert rows[0]["season_driving_distance"] == 315.5

    def test_no_snapshots_still_produces_season_columns_as_none(self):
        events = [_event("E1", "2026-08-15", [_participant("1")])]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        assert rows[0]["season_driving_distance"] is None


class TestBuildRoundDataset:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_builds_one_row_per_round_actually_played(self):
        events = [_event("E1", "2026-06-01", [_participant("1")], rounds_by_participant={
            "1": [_round(1, -2), _round(2, 0), _round(3, -1), _round(4, -1)],
        })]
        storage = self._storage(events)

        rows = build_dataset.build_round_dataset(storage, window=5)

        assert [r["round_number"] for r in rows] == [1, 2, 3, 4]

    def test_a_cut_golfer_only_produces_rounds_1_and_2(self):
        events = [_event("E1", "2026-06-01", [_participant("1")], rounds_by_participant={
            "1": [_round(1, 4), _round(2, 2)],
        })]
        storage = self._storage(events)

        rows = build_dataset.build_round_dataset(storage, window=5)

        assert [r["round_number"] for r in rows] == [1, 2]

    def test_same_round_history_is_scoped_by_round_number_across_tournaments(self):
        events = [
            _event("E1", "2026-06-01", [_participant("1")], rounds_by_participant={"1": [_round(1, -5), _round(2, 1)]}),
            _event("E2", "2026-06-08", [_participant("1")], rounds_by_participant={"1": [_round(1, -3), _round(2, -1)]}),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_round_dataset(storage, window=5)

        e2_round1 = next(r for r in rows if r["event_key"] == "E2" and r["round_number"] == 1)
        e2_round2 = next(r for r in rows if r["event_key"] == "E2" and r["round_number"] == 2)
        assert e2_round1["same_round_avg_score_to_par"] == -5  # only E1's own round 1
        assert e2_round2["same_round_avg_score_to_par"] == 1  # only E1's own round 2


class TestBuildCutlineDataset:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_builds_one_row_per_tournament_no_golfer_dimension(self):
        events = [
            _event("E1", "2026-06-01", [_participant("1"), _participant("2")], cut_score=-2, cut_count=71),
            _event("E2", "2026-06-08", [_participant("1")], cut_score=0, cut_count=0),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_cutline_dataset(storage)

        assert {r["event_key"] for r in rows} == {"E1", "E2"}

    def test_course_cut_history_carries_forward_across_tournaments_at_the_same_course(self):
        events = [
            _event("E1", "2025-06-01", [_participant("1")], course_id="65", cut_score=-4, cut_count=70),
            _event("E2", "2026-06-01", [_participant("1")], course_id="65", cut_score=-2, cut_count=71),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_cutline_dataset(storage)

        e2_row = next(r for r in rows if r["event_key"] == "E2")
        assert e2_row["course_avg_cut_score"] == -4


def _match_play_event(event_key, event_date, parent_event_id, home_golfer_ids, away_golfer_ids, match_format="foursome", home_won=True):
    return {
        "event_key": event_key, "event_date": event_date, "event_type": "match_play",
        "parent_event_id": parent_event_id, "match_format": match_format,
        "participants": [
            {"entity_id": "1", "role": "home", "golfer_entity_ids": home_golfer_ids, "result": {"won": home_won, "halved": False}},
            {"entity_id": "3", "role": "away", "golfer_entity_ids": away_golfer_ids, "result": {"won": not home_won, "halved": False}},
        ],
    }


def _cup_event(event_key, event_date, home_won=True):
    return {
        "event_key": event_key, "event_id": event_key, "event_date": event_date, "event_type": "cup", "tournament_name": "Presidents Cup",
        "participants": [
            {"entity_id": "1", "role": "home", "result": {"won": home_won, "halved": False}},
            {"entity_id": "3", "role": "away", "result": {"won": not home_won, "halved": False}},
        ],
    }


class TestBuildMatchAndCupDatasets:
    def _storage(self, events):
        storage = MagicMock()
        storage.get_all_events.return_value = events
        return storage

    def test_one_match_row_per_match_play_event(self):
        events = [_match_play_event("E1-match-1", "2022-09-22", "E1", ["10"], ["20"])]
        storage = self._storage(events)

        match_rows, cup_rows = build_dataset.build_match_and_cup_datasets(storage, window=5)

        assert [r["event_key"] for r in match_rows] == ["E1-match-1"]
        assert cup_rows == []

    def test_one_cup_row_per_cup_event(self):
        events = [_cup_event("E1", "2022-09-22")]
        storage = self._storage(events)

        match_rows, cup_rows = build_dataset.build_match_and_cup_datasets(storage, window=5)

        assert match_rows == []
        assert [r["event_key"] for r in cup_rows] == ["E1"]

    def test_match_row_sees_only_prior_field_history_not_future(self):
        events = [
            _event("F1", "2026-06-01", [_participant("10", finish_position=1, score_to_par=-10)]),
            _match_play_event("E1-match-1", "2026-09-22", "E1", ["10"], ["20"]),
            _event("F2", "2026-10-01", [_participant("10", finish_position=50, score_to_par=10)]),
        ]
        storage = self._storage(events)

        match_rows, _ = build_dataset.build_match_and_cup_datasets(storage, window=5)

        # Only F1 (before the match) contributes -- F2 (after) must not leak in.
        assert match_rows[0]["home_avg_score_to_par"] == -10

    def test_match_play_results_do_not_feed_golfer_history(self):
        # A match win/loss isn't a stroke score -- it must never appear
        # as history for a LATER regular-tour event's own rolling form.
        events = [
            _match_play_event("E1-match-1", "2026-06-01", "E1", ["10"], ["20"]),
            _event("F1", "2026-07-01", [_participant("10", finish_position=3, score_to_par=-5)]),
        ]
        storage = self._storage(events)

        rows = build_dataset.build_golfer_dataset(storage, window=5)

        f1_row = next(r for r in rows if r["event_key"] == "F1")
        assert f1_row["events_played"] == 0

    def test_cup_roster_derived_from_union_of_its_own_matches(self):
        events = [
            _event("F1", "2026-05-01", [_participant("10", finish_position=1, score_to_par=-8), _participant("11", finish_position=2, score_to_par=-6)]),
            _match_play_event("E1-match-1", "2026-09-01", "E1", ["10"], ["20"], match_format="foursome"),
            _match_play_event("E1-match-2", "2026-09-01", "E1", ["11"], ["21"], match_format="singles"),
            _cup_event("E1", "2026-09-01"),
        ]
        storage = self._storage(events)

        _, cup_rows = build_dataset.build_match_and_cup_datasets(storage, window=5)

        # Roster is golfer 10 AND golfer 11 (from both matches), so the
        # home team's average blends both golfers' prior form: -8 and -6.
        assert cup_rows[0]["home_avg_score_to_par"] == -7

    def test_no_matches_for_a_cup_gives_empty_roster_not_an_error(self):
        # _average_side([]) returns {} (no home_* keys at all), not a
        # dict of Nones -- see its own docstring for why that's
        # equivalent once assembled into a DataFrame alongside rows that
        # DO have a roster.
        events = [_cup_event("E1", "2026-09-01")]
        storage = self._storage(events)

        _, cup_rows = build_dataset.build_match_and_cup_datasets(storage, window=5)

        assert cup_rows[0].get("home_avg_score_to_par") is None

    def test_individual_match_play_produces_no_cup_rows(self):
        # WGC-Dell Technologies Match Play -- match rows only, no
        # parent-level Cup event ever written for it.
        events = [
            _match_play_event("W1-match-1", "2022-03-27", "W1", ["3439"], ["3448"], match_format="singles"),
        ]
        storage = self._storage(events)

        match_rows, cup_rows = build_dataset.build_match_and_cup_datasets(storage, window=5)

        assert len(match_rows) == 1
        assert cup_rows == []


class TestWriteDataset:
    def test_raises_on_empty_rows(self):
        s3 = MagicMock()
        with pytest.raises(RuntimeError):
            build_dataset._write_dataset(s3, "bucket", "some/key.parquet", [], "test")

    def test_writes_a_non_empty_dataset(self):
        s3 = MagicMock()
        build_dataset._write_dataset(s3, "bucket", "some/key.parquet", [{"a": 1}], "test")
        s3.put_bytes.assert_called_once()
        assert s3.put_bytes.call_args.args[0] == "some/key.parquet"
