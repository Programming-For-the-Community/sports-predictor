"""
Thin wrapper around a single boto3 DynamoDB Table resource. Each sport
adapter's storage module instantiates one of these per table instead of
repeating put_item/batch_writer boilerplate -- entities, events, and
player_game_stats become three DynamoDBTable instances, not three bespoke
modules.
"""
import logging
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Raised above botocore's default (10) to support concurrent writers.
_CONFIG = Config(max_pool_connections=25)


def _to_dynamodb_safe(value):
    """Recursively converts float -> Decimal (via str() to avoid binary
    float imprecision) so callers can write ordinary floats; DynamoDB's
    Number type has no native float."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_safe(v) for v in value]
    return value


def _from_dynamodb_safe(value):
    """Inverse of _to_dynamodb_safe -- converts every Decimal boto3 returns
    back to int (whole numbers) or float."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamodb_safe(v) for v in value]
    return value


class DynamoDBTable:
    def __init__(self, table_name: str, region: str | None = None):
        self.table_name = table_name
        self._table = boto3.resource("dynamodb", region_name=region, config=_CONFIG).Table(table_name)

    def put_item(self, item: dict, condition_expression=None) -> bool:
        """Writes item, returning True if written. condition_expression (a
        boto3.dynamodb.conditions expression) makes the write conditional --
        a ConditionalCheckFailedException returns False instead of raising."""
        kwargs = {"Item": _to_dynamodb_safe(item)}
        if condition_expression is not None:
            kwargs["ConditionExpression"] = condition_expression
        try:
            self._table.put_item(**kwargs)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_item(self, key: dict) -> dict | None:
        response = self._table.get_item(Key=key)
        item = response.get("Item")
        return _from_dynamodb_safe(item) if item is not None else None

    def delete_item(self, key: dict) -> None:
        self._table.delete_item(Key=key)

    def batch_write(self, items: list[dict], key_names: list[str]) -> None:
        if not items:
            return
        with self._table.batch_writer(overwrite_by_pkeys=key_names) as batch:
            for item in items:
                batch.put_item(Item=_to_dynamodb_safe(item))

    def query(
        self,
        key_condition,
        index_name: str | None = None,
        scan_index_forward: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """key_condition is a boto3.dynamodb.conditions expression built by
        the caller. Paginates until every matching item is collected or
        `limit` is reached; `limit` caps the returned count, not the
        internal page size."""
        kwargs = {"KeyConditionExpression": key_condition, "ScanIndexForward": scan_index_forward}
        if index_name is not None:
            kwargs["IndexName"] = index_name

        items: list[dict] = []
        response = self._table.query(**kwargs)
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response and (limit is None or len(items) < limit):
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
        items = items[:limit] if limit is not None else items
        return [_from_dynamodb_safe(item) for item in items]

    def scan(self, filter_expression=None) -> list[dict]:
        """Paginates through the entire table, logging progress after each
        page (each capped around 1MB by DynamoDB regardless of item count).
        Not meant for hot paths."""
        kwargs = {}
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression

        items: list[dict] = []
        page = 1
        response = self._table.scan(**kwargs)
        items.extend(response.get("Items", []))
        logger.info("Scanning %s: page %d, %d items so far", self.table_name, page, len(items))
        while "LastEvaluatedKey" in response:
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            page += 1
            logger.info("Scanning %s: page %d, %d items so far", self.table_name, page, len(items))
        return [_from_dynamodb_safe(item) for item in items]
