# EventBridge Scheduler resource that directly invokes the
# ncaafb-schedule-sync Lambda (lambda-ncaafb-schedule-sync.tf), not routed
# through Step Functions. The Lambda walks every week of the current
# season internally in one invocation, sharing a single CFBDClient/rate
# limiter for the whole run.
#
# A REAL pre-existing gap, not a new addition -- the Lambda itself (and
# its Terraform resource) has existed since NCAAFB onboarding, its own
# docstring and lambda-ncaafb-schedule-sync.tf's own comment both already
# said "Triggered by EventBridge Scheduler -- see scheduler-ncaafb-
# schedule-sync.tf", and ncaafb_data_pipeline.yml has been deploying real,
# tested code to it on every push -- but this scheduler resource was
# simply never created, so nothing has ever actually invoked it. Found
# 2026-08-31 while fixing NFL/PGA's own schedule-sync-vs-season-projection
# ordering (see those files' own comments) and noticing NCAAFB had no
# schedule-sync scheduler to check the ordering of at all.
#
# Weekly, Thursday 10:00 UTC -- same day as, and 4 hours before,
# scheduler-ncaafb-season-projection.tf (Thu 14:00 UTC), so that week's
# projection always uses a freshly synced schedule from the very start
# (built correctly the first time, rather than needing the same after-
# the-fact reorder NFL/PGA's own schedulers just needed).
resource "aws_scheduler_schedule" "ncaafb_schedule_sync" {
  name        = "${var.project}-ncaafb-schedule-sync"
  description = "Invokes the ncaafb-schedule-sync Lambda weekly, Thu 10:00 UTC, to seed/refresh every week of the current NCAAFB season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? * THU *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ncaafb_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
