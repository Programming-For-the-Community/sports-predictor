# EventBridge Scheduler resource that directly invokes the
# pga-schedule-sync Lambda (lambda-pga-schedule-sync.tf), not routed
# through Step Functions. The Lambda discovers and syncs the whole
# season's calendar internally in one invocation.
#
# Weekly, Tuesday 10:00 UTC -- not Monday: PGA tournaments are scheduled
# to conclude Sunday, but a weather delay can push final-round (or
# sudden-death playoff) holes to Monday, and a Monday run risks landing
# mid-delay and capturing the tournament still in progress rather than
# final. Tuesday gives that Monday spillover a full day to actually
# finish before this Lambda's own refresh-window bookkeeping treats the
# tournament as settled (daily ingest still refreshes it sooner in the
# normal case -- see lambda-pga-ingest.tf -- this is the backstop for the
# delayed one). Year-round, not gated to a season window (PGA has none --
# see dynamodb-sport-registry.tf's pga_registry row), so this always
# runs.
#
# scheduler-pga-season-projection.tf runs the same Tuesday, 4 hours after
# this (14:00 UTC) -- moved there 2026-08-31 specifically so that weekly
# projection always uses this same day's freshly synced calendar, not a
# stale one from up to 6 days earlier.
resource "aws_scheduler_schedule" "pga_schedule_sync" {
  name        = "${var.project}-pga-schedule-sync"
  description = "Invokes the pga-schedule-sync Lambda weekly, Tue 10:00 UTC, to seed/refresh the current PGA season's tournament calendar."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * TUE *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pga_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
