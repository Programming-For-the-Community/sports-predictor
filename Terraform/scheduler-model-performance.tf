# Daily at 15:00 UTC (11am ET): after the 10:00 UTC daily ingest (see
# scheduler-ingest-orchestrator.tf) has landed the previous day's results and
# normalize has written them, so the scorecard includes last night's games.
resource "aws_scheduler_schedule" "model_performance" {
  name        = "${var.project}-model-performance"
  description = "Runs the model-performance Lambda daily to rebuild every head-to-head sport's scorecard."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 15 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.model_performance.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({})
  }
}
