"""
Unit tests for DynamoDBTable's query/scan pagination and passthrough
behavior, added for feature engineering's read path. put_item/batch_write
predate this file and aren't covered here -- get_item is, but only for
its Decimal-conversion behavior (see TestDecimalConversion), added after
a real feature-engineering run crashed on json.dumps(Decimal(...)) while
building player-prop training rows from a live DynamoDB read.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from library.aws.dynamodb_table import DynamoDBTable, _from_dynamodb_safe


def _make_table(
    query_pages: list[dict] | None = None,
    scan_pages: list[dict] | None = None,
    get_item_response: dict | None = None,
):
    mock_boto_table = MagicMock()
    if query_pages is not None:
        mock_boto_table.query.side_effect = query_pages
    if scan_pages is not None:
        mock_boto_table.scan.side_effect = scan_pages
    if get_item_response is not None:
        mock_boto_table.get_item.return_value = get_item_response

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


class TestDecimalConversion:
    """boto3's Table resource returns every DynamoDB Number as Decimal.
    Decimal arithmetic-combines silently with itself (sum()/len() never
    raises), so a Decimal can flow through an entire feature-computation
    pipeline undetected until something that actually rejects it --
    json.dumps, in the crash this was written to cover -- finally raises.
    These tests confirm get_item/query/scan convert it back before a
    caller ever sees it.
    """

    def test_from_dynamodb_safe_converts_whole_number_to_int(self):
        assert _from_dynamodb_safe(Decimal("312")) == 312
        assert isinstance(_from_dynamodb_safe(Decimal("312")), int)

    def test_from_dynamodb_safe_converts_fractional_to_float(self):
        assert _from_dynamodb_safe(Decimal("7.1")) == 7.1
        assert isinstance(_from_dynamodb_safe(Decimal("7.1")), float)

    def test_from_dynamodb_safe_recurses_through_nested_dicts_and_lists(self):
        value = {
            "stat_line": {"passing_yards": Decimal("312"), "completion_pct": Decimal("67.5")},
            "scores": [Decimal("27"), Decimal("20")],
        }

        result = _from_dynamodb_safe(value)

        assert result == {
            "stat_line": {"passing_yards": 312, "completion_pct": 67.5},
            "scores": [27, 20],
        }
        assert isinstance(result["stat_line"]["passing_yards"], int)
        assert isinstance(result["stat_line"]["completion_pct"], float)

    def test_from_dynamodb_safe_leaves_non_decimal_values_untouched(self):
        assert _from_dynamodb_safe("KC") == "KC"
        assert _from_dynamodb_safe(True) is True
        assert _from_dynamodb_safe(None) is None

    def test_get_item_converts_decimal_in_response(self):
        table, _ = _make_table(get_item_response={"Item": {"entity_id": "KC", "score": Decimal("27")}})

        result = table.get_item({"entity_id": "KC"})

        assert result == {"entity_id": "KC", "score": 27}
        assert isinstance(result["score"], int)

    def test_get_item_returns_none_when_missing(self):
        table, _ = _make_table(get_item_response={})

        assert table.get_item({"entity_id": "missing"}) is None

    def test_query_converts_decimal_in_nested_stat_line(self):
        table, _ = _make_table(query_pages=[
            {"Items": [{"entity_id": "mahomes-patrick", "stat_line": {"passing_yards": Decimal("312")}}]},
        ])

        result = table.query(object())

        assert result[0]["stat_line"]["passing_yards"] == 312
        assert isinstance(result[0]["stat_line"]["passing_yards"], int)

    def test_scan_converts_decimal_across_paginated_results(self):
        table, _ = _make_table(scan_pages=[
            {"Items": [{"id": "1", "score": Decimal("27")}], "LastEvaluatedKey": {"id": "1"}},
            {"Items": [{"id": "2", "score": Decimal("20.5")}]},
        ])

        result = table.scan()

        assert result[0]["score"] == 27 and isinstance(result[0]["score"], int)
        assert result[1]["score"] == 20.5 and isinstance(result[1]["score"], float)
