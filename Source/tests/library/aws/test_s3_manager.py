"""
Unit tests for S3Manager.
"""
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from library.aws.s3_manager import S3Manager


TEST_OWNER = "123456789012"


def _make_manager(pages: list[dict] | None = None):
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = pages if pages is not None else [{}]
    mock_client.get_paginator.return_value = mock_paginator

    with patch("library.aws.s3_manager.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        # expected_bucket_owner passed explicitly so construction never
        # calls the real get_account_id() (sts:GetCallerIdentity).
        manager = S3Manager("test-bucket", region="us-east-1", expected_bucket_owner=TEST_OWNER)
    return manager, mock_client, mock_paginator


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "HeadObject")


class TestListKeys:
    def test_returns_keys_from_a_single_page(self):
        manager, _, _ = _make_manager([{"Contents": [{"Key": "nfl/v1/model.xgb"}, {"Key": "nfl/v1/metadata.json"}]}])

        result = manager.list_keys("nfl/")

        assert result == ["nfl/v1/model.xgb", "nfl/v1/metadata.json"]

    def test_flattens_across_multiple_pages(self):
        manager, _, _ = _make_manager([
            {"Contents": [{"Key": "nfl/v1/model.xgb"}]},
            {"Contents": [{"Key": "nfl/v2/model.xgb"}]},
        ])

        result = manager.list_keys("nfl/")

        assert result == ["nfl/v1/model.xgb", "nfl/v2/model.xgb"]

    def test_returns_empty_list_when_prefix_has_no_objects(self):
        manager, _, _ = _make_manager([{}])

        assert manager.list_keys("nfl/nonexistent/") == []

    def test_passes_bucket_and_prefix_to_paginator(self):
        manager, _, mock_paginator = _make_manager([{}])

        manager.list_keys("nfl/win-probability/")

        mock_paginator.paginate.assert_called_once_with(
            Bucket="test-bucket", Prefix="nfl/win-probability/", ExpectedBucketOwner=TEST_OWNER,
        )


class TestObjectExists:
    def test_returns_true_when_head_object_succeeds(self):
        manager, mock_client, _ = _make_manager()

        assert manager.object_exists("nfl/v1/model.xgb") is True
        mock_client.head_object.assert_called_once_with(
            Bucket="test-bucket", Key="nfl/v1/model.xgb", ExpectedBucketOwner=TEST_OWNER,
        )

    @pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
    def test_returns_false_for_not_found_error_codes(self, code):
        manager, mock_client, _ = _make_manager()
        mock_client.head_object.side_effect = _client_error(code)

        assert manager.object_exists("nfl/v1/model.xgb") is False

    def test_reraises_other_client_errors(self):
        manager, mock_client, _ = _make_manager()
        mock_client.head_object.side_effect = _client_error("AccessDenied")

        with pytest.raises(ClientError):
            manager.object_exists("nfl/v1/model.xgb")


class TestPutJson:
    def test_serializes_payload_and_passes_bucket_and_key(self):
        manager, mock_client, _ = _make_manager()

        manager.put_json("nfl/v1/metadata.json", {"version": 1})

        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="nfl/v1/metadata.json",
            Body=b'{"version": 1}',
            ContentType="application/json",
            ExpectedBucketOwner=TEST_OWNER,
        )


class TestGetJson:
    def test_deserializes_response_body(self):
        manager, mock_client, _ = _make_manager()
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"version": 1}')}

        result = manager.get_json("nfl/v1/metadata.json")

        assert result == {"version": 1}
        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="nfl/v1/metadata.json", ExpectedBucketOwner=TEST_OWNER,
        )


class TestPutBytes:
    def test_passes_data_and_content_type(self):
        manager, mock_client, _ = _make_manager()

        manager.put_bytes("nfl/v1/model.xgb", b"raw-bytes", content_type="application/octet-stream")

        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="nfl/v1/model.xgb",
            Body=b"raw-bytes",
            ContentType="application/octet-stream",
            ExpectedBucketOwner=TEST_OWNER,
        )

    def test_defaults_content_type_to_octet_stream(self):
        manager, mock_client, _ = _make_manager()

        manager.put_bytes("nfl/v1/model.xgb", b"raw-bytes")

        assert mock_client.put_object.call_args.kwargs["ContentType"] == "application/octet-stream"


class TestGetBytes:
    def test_returns_response_body_bytes(self):
        manager, mock_client, _ = _make_manager()
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"raw-bytes")}

        result = manager.get_bytes("nfl/v1/model.xgb")

        assert result == b"raw-bytes"
        mock_client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="nfl/v1/model.xgb", ExpectedBucketOwner=TEST_OWNER,
        )


class TestDeleteObject:
    """Supports clearing a resumable-progress breadcrumb once a
    library.ml.backtest.run_backtest run finishes (see
    library.ml.training_common.clear_run_progress)."""

    def test_passes_bucket_and_key_to_delete_object(self):
        manager, mock_client, _ = _make_manager([{}])

        manager.delete_object("training-runs/nfl/win-probability/run-1/progress.json")

        mock_client.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="training-runs/nfl/win-probability/run-1/progress.json",
            ExpectedBucketOwner=TEST_OWNER,
        )
