# EventBridge Scheduler resource that starts the training-orchestrator
# state machine (sfn-training-orchestrator.tf) -- the state machine's own
# Map states handle the per-sport/per-target fan-out, so this is the only
# schedule needed for NFL training.
#
# Weekly, Wednesday 12:00 UTC, year-round -- an inactive sport is filtered
# out by the state machine's own registry scan (`active`, see
# dynamodb-sport-registry.tf), the same season-gating mechanism the
# ingest schedule uses.
resource "aws_scheduler_schedule" "training_orchestrator" {
  name        = "${var.project}-training-orchestrator"
  description = "Starts the training-orchestrator state machine weekly, Wed 12:00 UTC, year-round -- season gating is per-sport via the registry's active flag."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 12 ? * WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.training_orchestrator.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}