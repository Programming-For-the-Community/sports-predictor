# Invokes pga_predict weekly to recompute the FedEx Cup season simulation
# (standings + FedEx St. Jude/BMW Championship/TOUR Championship field
# probabilities + Champion probability) and write it to S3. GET /pga/season
# serves the cached result.
#
# Saturday 14:00 UTC -- distinct from every other sport's own weekly
# season-projection schedule (NBA Fri, NCAAFB Thu, NFL Wed, NCAAMBB daily);
# no real collision constraint, Saturday also lands mid-tournament during
# an active PGA Tour week. aws_iam_role.eventbridge_invoke already covers
# pga_predict's own ARN (iam-eventbridge-invoke.tf) -- no IAM edit needed
# here.
resource "aws_scheduler_schedule" "pga_season_projection" {
  name        = "${var.project}-pga-season-projection"
  description = "Invokes the pga_predict Lambda weekly, Sat 14:00 UTC, to recompute the FedEx Cup season simulation and cache it to S3 for GET /pga/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * SAT *)"
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
