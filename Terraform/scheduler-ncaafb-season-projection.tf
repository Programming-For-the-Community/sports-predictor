# Invokes ncaafb_predict weekly to recompute the season projection
# (standings + bowl/playoff/championship probabilities + player-prop
# leaderboards) and write it to S3. GET /ncaafb/season serves the cached
# result. Mirrors scheduler-nfl-season-projection.tf.
#
# Thursday 14:00 UTC (not tied to NCAAFB's monthly training cadence,
# unlike NFL's weekly one) -- purely offset from NFL's own Wednesday slot
# to spread load.
resource "aws_scheduler_schedule" "ncaafb_season_projection" {
  name        = "${var.project}-ncaafb-season-projection"
  description = "Invokes the ncaafb_predict Lambda weekly, Thu 14:00 UTC, to recompute the season projection and cache it to S3 for GET /ncaafb/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * THU *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ncaafb_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
