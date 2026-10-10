# Invokes the nhl_live_scores Lambda every 60s to refresh its live-score
# cache. A tick with nothing near puck drop costs a single cheap DynamoDB
# Query (live_scores._candidate_events).
resource "aws_scheduler_schedule" "nhl_live_scores" {
  name        = "${var.project}-nhl-live-scores"
  description = "Invokes the nhl_live_scores Lambda every 60s to refresh its live-score cache for any event within 15 minutes of puck drop or still in progress."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(1 minute)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nhl_live_scores.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      detail-type = "LiveScoreRefresh"
    })
  }
}
