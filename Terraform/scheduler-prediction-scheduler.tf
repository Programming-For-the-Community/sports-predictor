# Drives the prediction-scheduler Lambda every 5 minutes. The cadence is what
# makes its 15-minute snapshot window safe: an event is seen by at least two
# ticks (one to claim it, another to retry a failed attempt) before kickoff.
resource "aws_scheduler_schedule" "prediction_scheduler" {
  name        = "${var.project}-prediction-scheduler"
  description = "Runs the prediction-scheduler Lambda every 5 minutes (pre-kickoff refresh + snapshot for head-to-head sports)."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.prediction_scheduler.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({})
  }
}
