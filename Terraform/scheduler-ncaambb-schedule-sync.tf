# EventBridge Scheduler resource that directly invokes the
# ncaambb-schedule-sync Lambda (lambda-ncaambb-schedule-sync.tf), not
# routed through Step Functions. The Lambda walks the season's schedule
# internally in one invocation.
#
# Weekly, Saturday 10:00 UTC (NFL is Thursday, NBA is Friday -- spread
# across different days so their schedule-sync runs don't all land at
# once). Year-round, not gated to the season, so it seeds next season's
# schedule ahead of opening night.
resource "aws_scheduler_schedule" "ncaambb_schedule_sync" {
  name        = "${var.project}-ncaambb-schedule-sync"
  description = "Invokes the ncaambb-schedule-sync Lambda weekly, Sat 10:00 UTC, to seed/refresh the current NCAA MBB season's schedule."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * SAT *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ncaambb_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
