"""
Unit tests for DynamoDBTable's query/scan pagination and passthrough
behavior, added for feature engineering's read path. put_item's plain
(unconditional) path predates this file and isn't covered here --
TestConditionalPutItem below covers its ConditionExpression support,
added for PipelineStorage.upsert_player_entity. get_item is covered, but
only for its Decimal-conversion behavior (see TestDecimalConversion),
added after a real feature-engineering run crashed on
json.dumps(Decimal(...)) while building player-prop training rows from a
live DynamoDB read.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

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


def _make_table_for_batch_get(batch_get_responses: list[dict]):
    """Separate from _make_table -- batch_get_item is called on the
    service RESOURCE (self._resource), not the Table object every other
    method here uses, so this exposes that mock instead."""
    with patch("library.aws.dynamodb_table.boto3") as mock_boto3:
        mock_resource = mock_boto3.resource.return_value
        mock_resource.batch_get_item.side_effect = batch_get_responses
        table = DynamoDBTable("test-table", region="us-east-1")
    return table, mock_resource


class TestConditionalPutItem:
    def test_unconditional_write_returns_true(self):
        table, mock_boto_table = _make_table()

        result = table.put_item({"entity_id": "12"})

        mock_boto_table.put_item.assert_called_once_with(Item={"entity_id": "12"})
        assert result is True

    def test_condition_expression_is_passed_through(self):
        table, mock_boto_table = _make_table()
        condition = object()

        table.put_item({"entity_id": "12"}, condition_expression=condition)

        mock_boto_table.put_item.assert_called_once_with(
            Item={"entity_id": "12"}, ConditionExpression=condition,
        )

    def test_conditional_check_failure_returns_false_not_raise(self):
        table, mock_boto_table = _make_table()
        mock_boto_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "boom"}}, "PutItem",
        )

        result = table.put_item({"entity_id": "12"}, condition_expression=object())

        assert result is False

    def test_other_client_errors_still_propagate(self):
        table, mock_boto_table = _make_table()
        mock_boto_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "boom"}}, "PutItem",
        )

        try:
            table.put_item({"entity_id": "12"}, condition_expression=object())
            assert False, "expected ClientError to propagate"
        except ClientError:
            pass


class TestDeleteItem:
    def test_passes_key_through(self):
        table, mock_boto_table = _make_table()

        table.delete_item({"entity_key": "SPORT#NBA#ENTITY#25"})

        mock_boto_table.delete_item.assert_called_once_with(Key={"entity_key": "SPORT#NBA#ENTITY#25"})


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

    def test_passes_limit_as_dynamodbs_own_page_limit(self):
        # Not just a client-side stop-early cap -- forwarded as DynamoDB's
        # own Limit so a bounded call reads less data server-side too,
        # otherwise a single page against a large partition can still cost
        # up to DynamoDB's own ~1MB page regardless of how small `limit` is.
        table, mock_boto_table = _make_table(query_pages=[{"Items": [{"id": "1"}]}])
        condition = object()

        table.query(condition, limit=50)

        mock_boto_table.query.assert_called_once_with(
            KeyConditionExpression=condition, ScanIndexForward=True, Limit=50,
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


class TestBatchGetItems:
    def test_empty_keys_returns_empty_without_calling_aws(self):
        table, mock_resource = _make_table_for_batch_get([])

        result = table.batch_get_items([])

        assert result == []
        mock_resource.batch_get_item.assert_not_called()

    def test_single_page_returns_responses_for_the_table(self):
        table, mock_resource = _make_table_for_batch_get([
            {"Responses": {"test-table": [{"entity_id": "KC"}, {"entity_id": "LAC"}]}},
        ])

        result = table.batch_get_items([{"entity_id": "KC"}, {"entity_id": "LAC"}])

        assert result == [{"entity_id": "KC"}, {"entity_id": "LAC"}]
        mock_resource.batch_get_item.assert_called_once_with(
            RequestItems={"test-table": {"Keys": [{"entity_id": "KC"}, {"entity_id": "LAC"}]}},
        )

    def test_retries_unprocessed_keys_until_none_remain(self):
        table, mock_resource = _make_table_for_batch_get([
            {
                "Responses": {"test-table": [{"entity_id": "KC"}]},
                "UnprocessedKeys": {"test-table": {"Keys": [{"entity_id": "LAC"}]}},
            },
            {"Responses": {"test-table": [{"entity_id": "LAC"}]}},
        ])

        with patch("library.aws.dynamodb_table.time.sleep") as mock_sleep:
            result = table.batch_get_items([{"entity_id": "KC"}, {"entity_id": "LAC"}])

        assert result == [{"entity_id": "KC"}, {"entity_id": "LAC"}]
        assert mock_resource.batch_get_item.call_count == 2
        mock_sleep.assert_called_once_with(1.5)  # _BATCH_GET_BACKOFF_BASE_SECONDS * 2**0

    def test_retry_backoff_doubles_each_attempt(self):
        # Never resolves -- forces every retry up to the cap so all of the
        # backoff sequence's sleeps are observable in one test.
        table, mock_resource = _make_table_for_batch_get([
            {"UnprocessedKeys": {"test-table": {"Keys": [{"entity_id": "LAC"}]}}},
        ] * 6)

        with patch("library.aws.dynamodb_table.time.sleep") as mock_sleep:
            try:
                table.batch_get_items([{"entity_id": "LAC"}])
                assert False, "expected RuntimeError once retries are exhausted"
            except RuntimeError:
                pass

        assert [call.args[0] for call in mock_sleep.call_args_list] == [1.5, 3.0, 6.0, 12.0, 24.0]

    def test_raises_after_exhausting_retries_with_keys_still_unprocessed(self):
        # A persistently-throttled batch must not retry forever -- confirms
        # the real bug (an unbounded `while request_items:` loop with no
        # cap) is fixed.
        table, mock_resource = _make_table_for_batch_get([
            {"UnprocessedKeys": {"test-table": {"Keys": [{"entity_id": "LAC"}]}}},
        ] * 6)

        with patch("library.aws.dynamodb_table.time.sleep"):
            try:
                table.batch_get_items([{"entity_id": "LAC"}])
                assert False, "expected RuntimeError once retries are exhausted"
            except RuntimeError as exc:
                assert "test-table" in str(exc)
                assert "5 retries" in str(exc)

        # Initial attempt + 5 retries = 6 calls, then it gives up.
        assert mock_resource.batch_get_item.call_count == 6

    def test_chunks_into_batches_of_100_keys(self):
        keys = [{"entity_id": str(i)} for i in range(150)]
        table, mock_resource = _make_table_for_batch_get([
            {"Responses": {"test-table": [{"entity_id": "a"}]}},
            {"Responses": {"test-table": [{"entity_id": "b"}]}},
        ])

        table.batch_get_items(keys)

        assert mock_resource.batch_get_item.call_count == 2
        first_call_keys = mock_resource.batch_get_item.call_args_list[0].kwargs["RequestItems"]["test-table"]["Keys"]
        second_call_keys = mock_resource.batch_get_item.call_args_list[1].kwargs["RequestItems"]["test-table"]["Keys"]
        assert len(first_call_keys) == 100
        assert len(second_call_keys) == 50

    def test_converts_decimal_in_response(self):
        table, _ = _make_table_for_batch_get([
            {"Responses": {"test-table": [{"entity_id": "KC", "score": Decimal("27")}]}},
        ])

        result = table.batch_get_items([{"entity_id": "KC"}])

        assert result == [{"entity_id": "KC", "score": 27}]
        assert isinstance(result[0]["score"], int)


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
