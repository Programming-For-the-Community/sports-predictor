"""
Unit tests for library.features.build_dataset_common.write_dataset -- the
empty-dataset guard every sport's feature-engineering main() relies on.
"""
import inspect
import logging
from unittest.mock import MagicMock

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

    def test_uploads_the_serialized_rows_and_clears_the_row_list(self):
        s3 = _s3()
        rows = [{"a": 1}, {"a": 2}]
        serialized = []

        def _serialize(rows_to_write):
            serialized.append(list(rows_to_write))
            return b"parquet-bytes"

        build_dataset_common.write_dataset(s3, "k.parquet", rows, "player", _logger, serialize=_serialize)

        assert serialized == [[{"a": 1}, {"a": 2}]]
        s3.put_bytes.assert_called_once_with("k.parquet", b"parquet-bytes", content_type="application/octet-stream")
        assert rows == []

    def test_serializes_to_parquet_by_default(self):
        default = inspect.signature(build_dataset_common.write_dataset).parameters["serialize"].default

        assert default is build_dataset_common.write_parquet

    def test_uses_a_custom_serializer_when_given(self):
        s3 = _s3()

        build_dataset_common.write_dataset(s3, "k", [{"a": 1}], "driver", _logger, serialize=lambda rows: b"custom")

        assert s3.put_bytes.call_args.args == ("k", b"custom")
