"""
Unit tests for DynamoDBTable's query/scan pagination and passthrough
behavior, added for feature engineering's read path. put_item/get_item/
batch_write predate this file and aren't covered here.
"""
from unittest.mock import MagicMock, patch

from library.aws.dynamodb_table import DynamoDBTable


def _make_table(query_pages: list[dict] | None = None, scan_pages: list[dict] | None = None):
    mock_boto_table = MagicMock()
    if query_pages is not None:
        mock_boto_table.query.side_effect = query_pages
    if scan_pages is not None:
        mock_boto_table.scan.side_effect = scan_pages

    with patch("library.aws.dynamodb_table.boto3") as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = mock_boto_table
        table = DynamoDBTable("test-table", region="us-east-1")
    return table, mock_boto_table


class TestQuery:
    def test_passes_key_condition_and_defaults(self):
        table, mock_boto_table = _make_table(query_pages=[{"Items": [{"id": "1"}]}])
        condition = object()

        result = table.query(condition)

        mock_boto_table.query.assert_called_once_with(
            KeyConditionExpression=condition, ScanIndexForward=True,
        )
        assert result == [{"id": "1"}]

    def test_passes_index_name_and_scan_index_forward(self):
        table, mock_boto_table = _make_table(query_pages=[{"Items": []}])
        condition = object()

        table.query(condition, index_name="entity-history", scan_index_forward=False)

        mock_boto_table.query.assert_called_once_with(
            KeyConditionExpression=condition, ScanIndexForward=False, IndexName="entity-history",
        )

    def test_paginates_until_no_last_evaluated_key(self):
        table, mock_boto_table = _make_table(query_pages=[
            {"Items": [{"id": "1"}], "LastEvaluatedKey": {"id": "1"}},
            {"Items": [{"id": "2"}]},
        ])

        result = table.query(object())

        assert result == [{"id": "1"}, {"id": "2"}]
        assert mock_boto_table.query.call_count == 2

    def test_stops_and_truncates_once_limit_reached(self):
        table, mock_boto_table = _make_table(query_pages=[
            {"Items": [{"id": "1"}, {"id": "2"}], "LastEvaluatedKey": {"id": "2"}},
            {"Items": [{"id": "3"}]},
        ])

        result = table.query(object(), limit=2)

        assert result == [{"id": "1"}, {"id": "2"}]
        assert mock_boto_table.query.call_count == 1


class TestScan:
    def test_passes_filter_expression(self):
        table, mock_boto_table = _make_table(scan_pages=[{"Items": []}])
        filter_expr = object()

        table.scan(filter_expression=filter_expr)

        mock_boto_table.scan.assert_called_once_with(FilterExpression=filter_expr)

    def test_omits_filter_expression_when_not_given(self):
        table, mock_boto_table = _make_table(scan_pages=[{"Items": []}])

        table.scan()

        mock_boto_table.scan.assert_called_once_with()

    def test_paginates_through_all_pages(self):
        table, mock_boto_table = _make_table(scan_pages=[
            {"Items": [{"id": "1"}], "LastEvaluatedKey": {"id": "1"}},
            {"Items": [{"id": "2"}]},
        ])

        result = table.scan()

        assert result == [{"id": "1"}, {"id": "2"}]
        assert mock_boto_table.scan.call_count == 2
