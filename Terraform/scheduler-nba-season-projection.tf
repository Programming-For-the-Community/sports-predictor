# Invokes nba_predict weekly to recompute the season projection (standings +
# play-in/playoff bracket probabilities + player-prop leaderboards) and
# write it to S3. GET /nba/season serves the cached result. Mirrors
# scheduler-ncaafb-season-projection.tf.
#
# Friday 14:00 UTC -- own day offset from NFL's Wednesday and NCAAFB's
# Thursday slots to spread load.
resource "aws_scheduler_schedule" "nba_season_projection" {
  name        = "${var.project}-nba-season-projection"
  description = "Invokes the nba_predict Lambda weekly, Fri 14:00 UTC, to recompute the season projection and cache it to S3 for GET /nba/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * FRI *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nba_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
