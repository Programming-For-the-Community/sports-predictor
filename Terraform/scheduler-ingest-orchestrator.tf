# EventBridge Scheduler resource that starts the ingest-orchestrator state
# machine (sfn-ingest-orchestrator.tf).
#
# Daily, year-round -- season gating lives on each sport's own registry
# row (`active`, see dynamodb-sport-registry.tf) rather than in the
# schedule itself, since a single shared schedule can't encode different
# sports' different season windows. A no-op day costs one Lambda
# invocation in the free tier, not a Fargate task.
resource "aws_scheduler_schedule" "ingest_orchestrator" {
  name        = "${var.project}-ingest-orchestrator"
  description = "Starts the ingest-orchestrator state machine daily at 10:00 UTC, year-round -- season gating is per-sport via the registry's active flag."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.ingest_orchestrator.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}