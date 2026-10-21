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

# Daily during the playoffs, so the bracket picks up each real result the
# morning after (14:30 UTC, after the 10:00 ingest). The predict Lambda
# skips the rebuild unless a postseason game is scheduled or just finished.
resource "aws_scheduler_schedule" "nhl_season_projection_postseason" {
  name        = "${var.project}-nhl-season-projection-postseason"
  description = "Invokes the nhl_predict Lambda daily, 14:30 UTC, to rebuild the season projection while the postseason is on."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(30 14 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nhl_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type     = "ScheduledSeasonProjection"
      postseason_only = true
    })
  }
}
