# Invokes pga_predict weekly to recompute the FedEx Cup season simulation
# (standings + FedEx St. Jude/BMW Championship/TOUR Championship field
# probabilities + Champion probability) and write it to S3. GET /pga/season
# serves the cached result.
#
# Tuesday 14:00 UTC -- moved from Monday 2026-08-31 so this lands AFTER,
# not before, that same week's schedule-sync (scheduler-pga-schedule-
# sync.tf, Tue 10:00 UTC, itself deliberately Tuesday rather than Monday
# for its own real reason -- see that file's own comment, not touched
# here): the original Monday slot meant season-projection always ran a
# full day BEFORE that week's own Tuesday sync, using a calendar up to 6
# days stale. Tuesday still lands inside the original Mon-Wed (not
# Thu-Sun, when a PGA Tour event is actually being played) window this
# schedule was chosen to stay within -- distinct from every other sport's
# own weekly season-projection schedule (NBA Fri, NCAAFB Thu, NFL Wed,
# NCAAMBB daily). aws_iam_role.eventbridge_invoke already covers
# pga_predict's own ARN (iam-eventbridge-invoke.tf) -- no IAM edit needed
# here.
resource "aws_scheduler_schedule" "pga_season_projection" {
  name        = "${var.project}-pga-season-projection"
  description = "Invokes the pga_predict Lambda weekly, Tue 14:00 UTC, to recompute the FedEx Cup season simulation and cache it to S3 for GET /pga/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * TUE *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pga_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
