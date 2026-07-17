"""
Thin wrapper around a single boto3 DynamoDB Table resource. Each sport
adapter's storage module instantiates one of these per table instead of
repeating put_item/batch_writer boilerplate -- entities, events, and
player_game_stats become three DynamoDBTable instances, not three bespoke
modules.
"""
import boto3


class DynamoDBTable:
    def __init__(self, table_name: str, region: str | None = None):
        self.table_name = table_name
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put_item(self, item: dict) -> None:
        self._table.put_item(Item=item)

    def get_item(self, key: dict) -> dict | None:
        response = self._table.get_item(Key=key)
        return response.get("Item")

    def batch_write(self, items: list[dict], key_names: list[str]) -> None:
        if not items:
            return
        with self._table.batch_writer(overwrite_by_pkeys=key_names) as batch:
            for item in items:
                batch.put_item(Item=item)
