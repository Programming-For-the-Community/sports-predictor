# EventBridge Scheduler resource that directly invokes the
# nba-schedule-sync Lambda (lambda-nba-schedule-sync.tf) -- direct invoke,
# not routed through Step Functions. The Lambda walks the season's
# schedule internally in one invocation.
#
# Weekly, Friday 10:00 UTC -- own day offset from NFL's Thursday slot
# (scheduler-nfl-schedule-sync.tf) to spread load; NBA's schedule itself
# doesn't change often enough to need more than a weekly refresh even
# though games happen nearly daily -- daily ingest (via the shared
# sfn-ingest-orchestrator) already keeps the current day's results fresh.
# Year-round, not gated to the season -- seeds next season's schedule ahead
# of opening night the same way NFL's own file does.
resource "aws_scheduler_schedule" "nba_schedule_sync" {
  name        = "${var.project}-nba-schedule-sync"
  description = "Invokes the nba-schedule-sync Lambda weekly, Fri 10:00 UTC, to seed/refresh the current NBA season's schedule."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * FRI *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nba_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
