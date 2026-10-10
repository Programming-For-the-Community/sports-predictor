# Daily, not weekly like NBA's: a game's probable starting goalies change
# day to day, and schedule-sync's refresh window is what re-reads them.
resource "aws_scheduler_schedule" "nhl_schedule_sync" {
  name        = "${var.project}-nhl-schedule-sync"
  description = "Invokes the nhl-schedule-sync Lambda daily at 09:00 UTC to seed the NHL schedule and refresh the next two weeks' games and probable goalies."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 9 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nhl_schedule_sync.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
