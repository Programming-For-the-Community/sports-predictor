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

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None, ReturnValues=None,
                    ExpressionAttributeNames=None):
        key = (Key["pk"], Key["sk"])
        item = self.items.setdefault(key, {})
        if Key["pk"].startswith("usage#"):
            return self._add_usage(item, ExpressionAttributeNames, ExpressionAttributeValues)
        if item.get("asked", 0) >= ExpressionAttributeValues[":limit"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        item["asked"] = item.get("asked", 0) + 1
        item["expires_at"] = ExpressionAttributeValues[":expires"]
        return {"Attributes": {"asked": item["asked"]}}

    @staticmethod
    def _add_usage(item, names, values):
        """usage.record's ADDs: counts, tokens, tool calls and the user set."""
        for field in ("questions", names["#outcome"]):
            item[field] = item.get(field, 0) + values[":one"]
        for field, value in (("input_tokens", ":input"), ("output_tokens", ":output"), ("tool_calls", ":tools")):
            item[field] = item.get(field, 0) + values[value]
        item["users"] = set(item.get("users", set())) | values[":user"]
        item["expires_at"] = values[":expires"]
        return {}

    def query(self, KeyConditionExpression):
        """pk equals and sk between, as usage.read asks."""
        pk_condition, sk_condition = KeyConditionExpression._values
        pk = pk_condition._values[1]
        low, high = sk_condition._values[1:]
        rows = [item | {"sk": sk} for (p, sk), item in sorted(self.items.items()) if p == pk and low <= sk <= high]
        return {"Items": rows}

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
