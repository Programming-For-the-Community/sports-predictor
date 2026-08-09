"""
Season-gate Lambda: given a sport's season_start/season_end ("MM-DD"),
reports whether today falls inside that window. Invoked synchronously by
both orchestrator state machines (Terraform/sfn-ingest-orchestrator.tf,
Terraform/sfn-training-orchestrator.tf) in place of the DynamoDB `active`
flag they used to read directly off the sport registry -- that flag was
runtime-mutable state living on a Terraform-managed item, so every
`terraform apply` silently reset it back to Terraform's declared value.
season_start/season_end are static, Terraform-owned config instead (see
Terraform/dynamodb-sport-registry.tf); this Lambda just recomputes
membership fresh on every invocation.

No AWS SDK calls -- the caller (Step Functions) already has the sport's
registry item in hand and passes season_start/season_end straight
through; this Lambda only ever does date math.
"""
from library.season import current_month_day, is_in_season


def lambda_handler(event, context):
    return {
        "in_season": is_in_season(current_month_day(), event["season_start"], event["season_end"]),
    }
