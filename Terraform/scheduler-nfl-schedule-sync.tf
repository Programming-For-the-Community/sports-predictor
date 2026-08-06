# EventBridge Scheduler resource that directly invokes the
# nfl-schedule-sync Lambda (lambda-nfl-schedule-sync.tf) -- direct invoke,
# not routed through Step Functions. The Lambda walks all 23 weeks
# internally in one invocation, sharing a single NFLClient/rate limiter
# for the whole run (see its own docstring).
#
# Weekly, Thursday 10:00 UTC -- well before that week's Thursday-night
# kickoff (~00:15 UTC Friday), so the frontend's upcoming-events list and
# season simulation are guaranteed fresh before any game in the coming
# week starts. Year-round, not gated to the season -- this is exactly what
# needs to run in the off-season to seed next season's schedule ahead of
# Week 1.
resource "aws_scheduler_schedule" "nfl_schedule_sync" {
  name        = "${var.project}-nfl-schedule-sync"
  description = "Invokes the nfl-schedule-sync Lambda weekly, Thu 10:00 UTC, to seed/refresh every week of the current NFL season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * THU *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
