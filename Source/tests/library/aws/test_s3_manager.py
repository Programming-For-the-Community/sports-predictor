"""
Unit tests for S3Manager.list_keys, added to support model-version
discovery (see library.storage.model_artifacts). The rest of S3Manager
predates this file and isn't covered here.
"""
from unittest.mock import MagicMock, patch

from library.aws.s3_manager import S3Manager


def _make_manager(pages: list[dict]):
    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = pages
    mock_client.get_paginator.return_value = mock_paginator

    with patch("library.aws.s3_manager.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        manager = S3Manager("test-bucket", region="us-east-1")
    return manager, mock_client, mock_paginator


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

        mock_paginator.paginate.assert_called_once_with(Bucket="test-bucket", Prefix="nfl/win-probability/")
