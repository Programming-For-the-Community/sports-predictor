import pytest
from botocore.exceptions import ClientError


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def put_json(self, key, payload):
        self.objects[key] = payload

    def get_json(self, key):
        return self.objects[key]

    def get_json_or_none(self, key):
        return self.objects.get(key)

    def list_keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]


@pytest.fixture
def fake_s3():
    """A dict-backed stand-in for library.aws.s3_manager.S3Manager."""
    return FakeS3


class FakeTable:
    def __init__(self):
        self.items = {}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues, ReturnValues):
        key = (Key["pk"], Key["sk"])
        item = self.items.setdefault(key, {})
        if item.get("asked", 0) >= ExpressionAttributeValues[":limit"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        item["asked"] = item.get("asked", 0) + 1
        item["expires_at"] = ExpressionAttributeValues[":expires"]
        return {"Attributes": {"asked": item["asked"]}}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item is not None else {}

    def put_item(self, Item):
        self.items[(Item["pk"], Item["sk"])] = Item

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)


@pytest.fixture
def fake_table():
    """A dict-backed stand-in for the assistant DynamoDB Table resource."""
    return FakeTable
