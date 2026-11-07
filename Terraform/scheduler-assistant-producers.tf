# Daily at 14:00 UTC: Cost Explorer has the previous day's spend by then.
# Each run also rewrites the two days before it, which Cost Explorer revises.
resource "aws_scheduler_schedule" "assistant_cost_snapshot" {
  name        = "${var.project}-assistant-cost-snapshot"
  description = "Runs the assistant cost-snapshot Lambda daily to refresh the project-only cost aggregates."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.assistant_cost_snapshot.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({})
  }
}

# Hourly at :10, after WAF has delivered the previous hour's records.
resource "aws_scheduler_schedule" "assistant_traffic_aggregate" {
  name        = "${var.project}-assistant-traffic-aggregate"
  description = "Runs the assistant traffic-aggregate Lambda hourly to count blocked and denied requests."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(10 * * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.assistant_traffic_aggregate.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({})
  }
}
