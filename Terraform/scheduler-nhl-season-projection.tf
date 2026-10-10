# Invokes nhl_predict weekly to recompute the season projection (standings +
# playoff/Stanley Cup probabilities + bracket + player-prop leaderboards)
# and write it to S3. GET /nhl/season serves the cached result.
#
# Friday 15:00 UTC.
resource "aws_scheduler_schedule" "nhl_season_projection" {
  name        = "${var.project}-nhl-season-projection"
  description = "Invokes the nhl_predict Lambda weekly, Fri 15:00 UTC, to recompute the season projection and cache it to S3 for GET /nhl/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 15 ? * FRI *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nhl_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
