# EventBridge Scheduler resource that directly invokes the
# nfl-schedule-sync Lambda (lambda-nfl-schedule-sync.tf), not routed
# through Step Functions. The Lambda walks all 23 weeks internally in one
# invocation, sharing a single NFLClient/rate limiter for the whole run.
#
# Weekly, Wednesday 10:00 UTC -- moved from Thursday 2026-08-31 so this
# lands BEFORE, not after, that same week's season-projection run
# (scheduler-nfl-season-projection.tf, Wed 14:00 UTC): the original
# Thursday slot meant season-projection was using a sync up to 6 days
# stale every single week (Wed 14:00 always fell before that week's own
# Thursday sync, not after it). Now sync(10:00) -> training-orchestrator
# (scheduler-training-orchestrator.tf, 12:00) -> season-projection
# (14:00) all land the same Wednesday, in the right order. Still well
# before that week's Thursday-night kickoff (~00:15 UTC Friday) -- if
# anything, more buffer than before. Year-round, not gated to the
# season, so it seeds next season's schedule ahead of Week 1.
resource "aws_scheduler_schedule" "nfl_schedule_sync" {
  name        = "${var.project}-nfl-schedule-sync"
  description = "Invokes the nfl-schedule-sync Lambda weekly, Wed 10:00 UTC, to seed/refresh every week of the current NFL season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
