# EventBridge Scheduler resource that starts the training-orchestrator
# state machine (sfn-training-orchestrator.tf) -- replaces
# scheduler-nfl-feature-engineering.tf, scheduler-nfl-train-win-probability-model.tf,
# and the deleted scheduler-nfl-train-score-model.tf/
# scheduler-nfl-train-player-prop-model.tf (4 files, 11 EventBridge
# Scheduler resources for NFL alone) with the one schedule below -- the
# state machine's own Map states do the per-sport/per-target fan-out that
# used to be encoded as separate schedule resources and hand-picked cron
# time slots.
#
# Weekly, Wednesday 12:00 UTC, year-round -- same day or already-established
# NFL cadence, but no longer scoped to Aug-Feb: an inactive sport is
# filtered out by the state machine's own registry scan (`active`, see
# dynamodb-sport-registry.tf), the same season-gating mechanism the ingest
# schedule now uses.
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