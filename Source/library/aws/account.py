"""
Resolves the current AWS account ID, for passing as ExpectedBucketOwner on
S3 calls. Read from the AWS_ACCOUNT_ID env var -- Terraform already knows
the account ID at deploy time (var.account_id, fed from the AWS_ACCOUNT_ID
GitHub Actions secret via tf_install.yml's TF_VAR_account_id), and every
Lambda/ECS task that constructs an S3Manager has it wired in. No STS
fallback: a missing env var means a compute resource Terraform forgot to
wire this into -- a deploy/CI configuration gap that should fail loudly and
immediately, not silently degrade into a runtime sts:GetCallerIdentity
call. That call is exactly what took down every predict Lambda's cold
start in production -- VPC-attached, no NAT Gateway, no STS VPC endpoint
at the time -- so it's deliberately not a fallback path here at all
anymore. Cached at module level.
"""
import os

_account_id: str | None = None


def get_account_id() -> str:
    global _account_id
    if _account_id is None:
        value = os.environ.get("AWS_ACCOUNT_ID")
        if not value:
            raise RuntimeError(
                "AWS_ACCOUNT_ID env var is not set. Every compute resource that "
                "constructs an S3Manager must have this wired in Terraform "
                "(var.account_id) -- this is a deploy/CI configuration gap, not "
                "something to paper over with a runtime STS call."
            )
        _account_id = value
    return _account_id
