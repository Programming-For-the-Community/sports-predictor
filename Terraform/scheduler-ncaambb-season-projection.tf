# Invokes ncaambb_predict daily to recompute the season projection
# (standings + national ranking + conference-tournament brackets + the
# March Madness bracket) and write it to S3. GET /ncaambb/season serves
# the cached result.
#
# Daily, not weekly like NFL/NCAAFB/NBA's own season-projection
# schedules -- NCAA MBB's ~150-game Saturdays and much larger tracked-
# team count mean standings/seeding can move a lot in a single day
# (especially during conference-tournament and Selection Sunday weeks),
# and this Lambda's own polling_cadence in the sport registry is already
# "daily" for the same reason. 14:00 UTC, after the previous night's
# games have long since been ingested and normalized.
resource "aws_scheduler_schedule" "ncaambb_season_projection" {
  name        = "${var.project}-ncaambb-season-projection"
  description = "Invokes the ncaambb_predict Lambda daily, 14:00 UTC, to recompute the season projection (standings, national ranking, conference brackets, March Madness bracket) and cache it to S3 for GET /ncaambb/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ncaambb_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
