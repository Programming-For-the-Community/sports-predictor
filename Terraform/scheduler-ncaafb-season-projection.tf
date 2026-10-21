# Invokes ncaafb_predict weekly to recompute the season projection
# (standings + bowl/playoff/championship probabilities + player-prop
# leaderboards) and write it to S3. GET /ncaafb/season serves the cached
# result.
#
# Thursday 14:00 UTC -- 4 hours after scheduler-ncaafb-schedule-sync.tf
# (Thu 10:00 UTC, added 2026-08-31; see that file's own comment for why
# NCAAFB had no schedule-sync scheduler to be ordered against at all
# until then), so this always uses that same day's freshly synced
# schedule.
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

# Daily during the playoffs, so the bracket picks up each real result the
# morning after (14:30 UTC, after the 10:00 ingest). The predict Lambda
# skips the rebuild unless a postseason game is scheduled or just finished.
resource "aws_scheduler_schedule" "ncaafb_season_projection_postseason" {
  name        = "${var.project}-ncaafb-season-projection-postseason"
  description = "Invokes the ncaafb_predict Lambda daily, 14:30 UTC, to rebuild the season projection while the postseason is on."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(30 14 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ncaafb_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type     = "ScheduledSeasonProjection"
      postseason_only = true
    })
  }
}
