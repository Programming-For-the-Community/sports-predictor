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

Also hands back its own X-Ray trace header (xray_trace_id/xray_parent_id
-- see library.aws.xray.current_trace_header), parsed from the env var
Lambda sets automatically once this function's own tracing_config is
Active (lambda-season-gate.tf). sfn-training-orchestrator.tf's own
CheckSeason state carries these through to RunFeatureEngineering's
ContainerOverrides, which is what lets that ECS task join this
invocation's own trace as a linked node in the X-Ray Trace Map -- ECS
isn't a native Step Functions/X-Ray integration, so nothing does this
propagation automatically. Empty strings, not omitted keys, when tracing isn't active: a missing
key would fail the ASL ResultSelector that reads it, and an empty
string is exactly what library.aws.xray.linked_segment_from_env's own
no-op check expects on the far end.
"""
from library.aws.xray import current_trace_header
from library.season import current_month_day, is_in_season


def lambda_handler(event, context):
    result = {
        "in_season": is_in_season(current_month_day(), event["season_start"], event["season_end"]),
    }
    trace_header = current_trace_header()
    result["xray_trace_id"], result["xray_parent_id"] = trace_header if trace_header else ("", "")
    return result
