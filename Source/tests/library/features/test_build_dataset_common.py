"""
Unit tests for library.features.build_dataset_common.write_dataset -- the
empty-dataset guard every sport's feature-engineering main() relies on.
"""
import io
import logging
from unittest.mock import MagicMock

import pandas as pd
import pytest

from library.features import build_dataset_common

_logger = logging.getLogger("test")


def _s3():
    s3 = MagicMock()
    s3.bucket = "models"
    return s3


class TestWriteDataset:
    def test_refuses_to_overwrite_with_an_empty_dataset(self):
        s3 = _s3()

        with pytest.raises(RuntimeError, match=r"player produced 0 rows -- refusing to overwrite s3://models/k\.parquet"):
            build_dataset_common.write_dataset(s3, "k.parquet", [], "player", _logger)
        s3.put_bytes.assert_not_called()

    def test_uploads_parquet_and_clears_the_row_list(self):
        s3 = _s3()
        rows = [{"a": 1, "stat_line": {"yds": 10}}, {"a": 2, "stat_line": {"yds": 20}}]

        build_dataset_common.write_dataset(s3, "k.parquet", rows, "player", _logger)

        key, body = s3.put_bytes.call_args.args
        assert key == "k.parquet"
        assert s3.put_bytes.call_args.kwargs == {"content_type": "application/octet-stream"}
        df = pd.read_parquet(io.BytesIO(body))
        assert df["a"].tolist() == [1, 2]
        assert df["stat_line"].tolist() == ['{"yds": 10}', '{"yds": 20}']
        assert rows == []

    def test_uses_a_custom_serializer_when_given(self):
        s3 = _s3()

        build_dataset_common.write_dataset(s3, "k", [{"a": 1}], "driver", _logger, serialize=lambda rows: b"custom")

        assert s3.put_bytes.call_args.args == ("k", b"custom")
