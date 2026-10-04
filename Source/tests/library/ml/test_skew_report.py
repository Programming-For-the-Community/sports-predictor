import importlib.util
import json
import math
import pathlib
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from library.ml import skew_report

EVENT = "SPORT#NFL#EVENT#1"


class TestCompareValues:
    @pytest.mark.parametrize("served, trained, expected", [
        (1.0, 1.0, None),
        (1.0, 1.0 + 1e-9, None),
        (None, math.nan, None),
        (1.0, 2.0, skew_report.MISMATCHED),
        (None, 2.0, skew_report.MISSING_AT_SERVING),
        (2.0, None, skew_report.MISSING_IN_TRAINING),
        (1.0, True, None),
    ])
    def test_outcomes(self, served, trained, expected):
        assert skew_report.compare_values(served, trained) == expected


class TestCaptureEntity:
    def test_participant_and_event_keys(self):
        assert skew_report.capture_entity("player-prop-sacks#v6#4242") == "4242"
        assert skew_report.capture_entity("win-probability#v10") is None


def _rows():
    events = pd.DataFrame({"event_key": [EVENT, "SPORT#NFL#EVENT#2"], "home_elo": [1510.0, 1490.0], "week": [3, 4]})
    players = pd.DataFrame({
        "event_key": [EVENT, EVENT], "entity_id": ["7", "8"], "avg_sacks": [0.5, 1.0], "games_this_season": [2, 2],
    })
    rounds = pd.DataFrame({
        "event_key": [EVENT, EVENT], "entity_id": ["7", "7"], "round_number": [1, 2], "same_round_avg": [-1.0, 0.5],
    })
    return skew_report.TrainingRows([events, players, rounds])


class TestTrainingRows:
    def test_an_event_row_comes_from_the_dataset_without_participants(self):
        assert _rows().find(EVENT, None, {"home_elo": 1.0})["home_elo"] == 1510.0

    def test_a_participant_row_matches_its_entity(self):
        assert _rows().find(EVENT, "8", {"avg_sacks": 0.0})["avg_sacks"] == 1.0

    def test_round_number_picks_one_of_a_golfers_rounds(self):
        assert _rows().find(EVENT, "7", {"same_round_avg": 0.0, "round_number": 2})["same_round_avg"] == 0.5

    def test_no_dataset_with_every_input_column_is_no_match(self):
        assert _rows().find(EVENT, "7", {"avg_sacks": 0.0, "not_a_column": 1.0}) is None

    def test_an_unknown_event_is_no_match(self):
        assert _rows().find("SPORT#NFL#EVENT#99", None, {"home_elo": 1.0}) is None


class TestBuildReport:
    def test_flags_only_columns_that_disagree_most_flagged_first(self):
        captured = {EVENT: {
            "win-probability#v10": {"home_elo": 1510.0, "week": 3},
            "player-prop-sacks#v6#7": {"avg_sacks": 0.9, "games_this_season": None},
            "player-prop-sacks#v6#8": {"avg_sacks": 0.0, "games_this_season": 2},
            "player-prop-sacks#v6#99": {"avg_sacks": 0.0},
        }}

        report = skew_report.build_report("nfl", captured, _rows())

        assert (report["events"], report["rows_matched"], report["rows_unmatched"], report["columns_compared"]) == (1, 3, 1, 4)
        assert [c["column"] for c in report["flagged_columns"]] == ["avg_sacks", "games_this_season"]
        sacks = report["flagged_columns"][0]
        assert (sacks["compared"], sacks["mismatched"]) == (2, 2)
        assert sacks["examples"][0] == {"event_key": EVENT, "capture": "player-prop-sacks#v6#7", "serving": 0.9, "training": 0.5}
        assert report["flagged_columns"][1]["missing_at_serving"] == 1

    def test_examples_are_capped(self):
        captured = {f"SPORT#NFL#EVENT#{n}": {"win-probability#v1": {"home_elo": 0.0}} for n in range(4)}
        rows = skew_report.TrainingRows([pd.DataFrame({"event_key": list(captured), "home_elo": [1.0] * 4})])

        report = skew_report.build_report("nfl", captured, rows)

        assert report["flagged_columns"][0]["mismatched"] == 4
        assert len(report["flagged_columns"][0]["examples"]) == skew_report.MAX_EXAMPLES


class TestLoading:
    def test_captured_files_are_keyed_by_event(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["serving-features/nfl/401.json", "serving-features/nfl/notes.txt"]
        s3.get_json.return_value = {"win-probability#v1": {"home_elo": 1.0}}

        assert skew_report.load_captured(s3, "nfl") == {"SPORT#NFL#EVENT#401": {"win-probability#v1": {"home_elo": 1.0}}}
        s3.list_keys.assert_called_once_with("serving-features/nfl/")

    def test_training_rows_load_every_dataset_of_the_sport(self):
        s3 = MagicMock()
        s3.list_keys.return_value = ["nfl/training-data/event_features.parquet", "nfl/training-data/README.md"]
        frame = pd.DataFrame({"event_key": [EVENT], "home_elo": [1510.0]})

        with patch.object(skew_report, "load_features", return_value=frame) as load:
            rows = skew_report.load_training_rows(s3, "nfl")

        load.assert_called_once_with(s3, "nfl/training-data/event_features.parquet")
        assert rows.find(EVENT, None, {"home_elo": 0.0})["home_elo"] == 1510.0


class TestSummary:
    def test_one_line_per_flagged_column(self):
        report = skew_report.build_report("nfl", {EVENT: {"win-probability#v1": {"home_elo": 1.0, "week": 3}}}, _rows())

        assert skew_report.summary(report) == (
            "nfl: 1 events, 1 rows matched, 0 unmatched, 1 of 2 columns flagged\n"
            "  home_elo: 1 mismatched, 0 missing at serving, 0 missing in training (of 1)"
        )


def _cli():
    path = pathlib.Path(__file__).parents[3] / "feature-engineering" / "skew_report.py"
    spec = importlib.util.spec_from_file_location("skew_report_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCli:
    def test_prints_the_summary_and_writes_the_full_report(self, tmp_path, capsys):
        cli = _cli()
        out = tmp_path / "report.json"

        with patch.object(cli, "S3Manager") as s3_manager, \
             patch.object(cli.skew_report, "load_captured", return_value={EVENT: {"win-probability#v1": {"home_elo": 1.0, "week": 3}}}), \
             patch.object(cli.skew_report, "load_training_rows", return_value=_rows()):
            cli.main(["nfl", "--bucket", "artifacts", "--out", str(out)])

        s3_manager.assert_called_once_with("artifacts")
        assert json.loads(out.read_text())["sport"] == "nfl"
        assert capsys.readouterr().out.startswith("nfl: 1 events")
