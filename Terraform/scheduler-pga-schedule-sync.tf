# EventBridge Scheduler resource that directly invokes the
# pga-schedule-sync Lambda (lambda-pga-schedule-sync.tf), not routed
# through Step Functions. The Lambda discovers and syncs the whole
# season's calendar internally in one invocation.
#
# Daily, 10:00 UTC. ESPN only publishes a tournament's field shortly
# before it starts, and normalize skips a stroke-play leaderboard with no
# competitors, so an upcoming tournament reaches the events table on the
# first run after its field appears. Year-round, not gated to a season
# window (PGA has none -- see dynamodb-sport-registry.tf's pga_registry
# row). scheduler-pga-season-projection.tf runs at 14:00 UTC, after this.
resource "aws_scheduler_schedule" "pga_schedule_sync" {
  name        = "${var.project}-pga-schedule-sync"
  description = "Invokes the pga-schedule-sync Lambda daily, 10:00 UTC, to seed/refresh the current PGA season's tournament calendar."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pga_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
