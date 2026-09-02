# Invokes f1_predict weekly to recompute the championship season
# simulation (driver + constructor standings/probabilities) and write it
# to S3. GET /f1/season serves the cached result.
#
# Tuesday 14:00 UTC -- shares PGA's own slot (scheduler-pga-season-
# projection.tf). F1 has no schedule-sync Lambda of its own -- Jolpica's
# full-season schedule call lives inside f1-ingest itself, refreshed
# daily.
resource "aws_scheduler_schedule" "f1_season_projection" {
  name        = "${var.project}-f1-season-projection"
  description = "Invokes the f1_predict Lambda weekly, Tue 14:00 UTC, to recompute the championship season simulation and cache it to S3 for GET /f1/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * TUE *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.f1_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
