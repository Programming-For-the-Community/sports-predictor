"""
Unit tests for library/ml/training_common.py's load_features -- split out
of test_training_common.py because this needs pyarrow (both directly and
transitively via pd.DataFrame.to_parquet), and pyarrow is only one of
training_common.py's CALLERS' own declared dependencies (feature-
engineering/model-training's requirements.txt), not one of library's own
(see training_common.load_features' own docstring for why its import of
pyarrow is local, not top-of-file). Source/tests/library/ is swept by CI
jobs that never install pyarrow (e.g. the predict Lambda's own test job)
-- importorskip keeps this file from breaking collection there, the same
way a module-level pyarrow import in training_common.py itself would.
"""
import io
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pyarrow = pytest.importorskip("pyarrow")

from library.ml import training_common  # noqa: E402


def _valid_parquet_bytes() -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame({"a": [1, 2]}).to_parquet(buffer)
    return buffer.getvalue()


class TestLoadFeatures:
    def test_returns_the_dataframe_on_a_clean_read(self):
        s3 = MagicMock()
        s3.get_bytes.return_value = _valid_parquet_bytes()

        df = training_common.load_features(s3, "nfl/training-data/event_features.parquet")

        assert list(df["a"]) == [1, 2]
        s3.get_bytes.assert_called_once()

    def test_retries_once_on_a_corrupt_read_then_succeeds(self):
        s3 = MagicMock()
        s3.get_bytes.side_effect = [b"not a parquet file", _valid_parquet_bytes()]

        with patch("library.ml.training_common.time.sleep"):
            df = training_common.load_features(s3, "nfl/training-data/event_features.parquet")

        assert list(df["a"]) == [1, 2]
        assert s3.get_bytes.call_count == 2

    def test_raises_after_exhausting_every_attempt(self):
        s3 = MagicMock()
        s3.get_bytes.return_value = b"not a parquet file"

        with patch("library.ml.training_common.time.sleep"):
            try:
                training_common.load_features(s3, "nfl/training-data/event_features.parquet")
                assert False, "expected RuntimeError"
            except RuntimeError as exc:
                assert isinstance(exc.__cause__, pyarrow.ArrowInvalid)

        assert s3.get_bytes.call_count == training_common.LOAD_FEATURES_MAX_ATTEMPTS
