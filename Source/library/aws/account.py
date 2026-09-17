"""
Resolves the current AWS account ID at runtime, for passing as
ExpectedBucketOwner on S3 calls. sts:GetCallerIdentity reflects the
caller's own identity and needs no IAM policy grant, so this works from
any Lambda's execution role with zero Terraform/IAM changes. Cached at
module level -- one extra API call per cold start, not per invocation.
"""
import boto3

from library.aws.boto_config import DEFAULT_CONFIG

_account_id: str | None = None


def get_account_id() -> str:
    global _account_id
    if _account_id is None:
        _account_id = boto3.client("sts", config=DEFAULT_CONFIG).get_caller_identity()["Account"]
    return _account_id
