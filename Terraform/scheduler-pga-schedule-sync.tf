# EventBridge Scheduler resource that directly invokes the
# pga-schedule-sync Lambda (lambda-pga-schedule-sync.tf), not routed
# through Step Functions. The Lambda discovers and syncs the whole
# season's calendar internally in one invocation.
#
# Weekly, Monday 10:00 UTC -- PGA tournaments conclude Sunday, so a
# Monday run picks up the just-finished tournament's final leaderboard a
# day ahead of it aging out of schedule-sync's own refresh window
# (though daily ingest already refreshes it sooner -- see lambda-pga-
# ingest.tf). Year-round, not gated to a season window (PGA has none --
# see dynamodb-sport-registry.tf's pga_registry row), so this always
# runs.
resource "aws_scheduler_schedule" "pga_schedule_sync" {
  name        = "${var.project}-pga-schedule-sync"
  description = "Invokes the pga-schedule-sync Lambda weekly, Mon 10:00 UTC, to seed/refresh the current PGA season's tournament calendar."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * MON *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pga_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
