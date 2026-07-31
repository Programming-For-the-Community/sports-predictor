"""
Thin wrapper around a single boto3 DynamoDB Table resource. Each sport
adapter's storage module instantiates one of these per table instead of
repeating put_item/batch_writer boilerplate -- entities, events, and
player_game_stats become three DynamoDBTable instances, not three bespoke
modules.
"""
from decimal import Decimal

import boto3


def _to_dynamodb_safe(value):
    """boto3's Table resource rejects Python float outright -- DynamoDB's
    Number type has no native float, and boto3 won't silently risk
    precision loss by accepting one. Every write goes through this so
    callers can build items with ordinary floats (e.g. parsed out of a
    stat line like "7.1") without needing to know that detail. Converts
    via str() rather than Decimal(float) directly: Decimal(7.1) captures
    the float's imprecise binary representation, not the value "7.1"
    actually means -- Decimal(str(7.1)) gets the right one.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_safe(v) for v in value]
    return value


class DynamoDBTable:
    def __init__(self, table_name: str, region: str | None = None):
        self.table_name = table_name
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put_item(self, item: dict) -> None:
        self._table.put_item(Item=_to_dynamodb_safe(item))

    def get_item(self, key: dict) -> dict | None:
        response = self._table.get_item(Key=key)
        return response.get("Item")

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
        the caller -- this class stays schema-agnostic, same as put_item/
        get_item above. Paginates until either every matching item is
        collected or `limit` is reached; `limit` caps the returned count,
        not the page size DynamoDB queries internally."""
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
        return items[:limit] if limit is not None else items

    def scan(self, filter_expression=None) -> list[dict]:
        """Paginates through the entire table. Only used where design/
        DATA_SCHEMA.md explicitly allows brute-forcing a query pattern
        instead of adding a GSI -- not meant for hot paths."""
        kwargs = {}
        if filter_expression is not None:
            kwargs["FilterExpression"] = filter_expression

        items: list[dict] = []
        response = self._table.scan(**kwargs)
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
        return items
