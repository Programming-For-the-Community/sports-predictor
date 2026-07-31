# EventBridge Scheduler resources that trigger the NFL ingest Lambda (see
# Terraform/lambda-nfl-ingest.tf for the function itself). Split into its
# own file since every sport adapter gets its own schedule as it's added,
# while Terraform/scheduler-group.tf holds the one schedule group shared
# across all of them.

resource "aws_lambda_permission" "eventbridge_invoke_nfl_ingest" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nfl_ingest.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.nfl_ingest.arn
}

# August through February, Mondays and Fridays at 00:00 UTC. One schedule
# covering the whole window (rather than a tighter in-season-only cadence)
# so a season start/end date shifting by a week or two never falls outside
# it -- ESPN having no games on a given day is a no-op fetch, not a problem
# worth optimizing away. Mon/Fri is plenty for how often the model actually
# gets retrained; bump this to a tighter cadence (or add a second schedule)
# if that changes. No schedule covers March through July, so the pipeline
# runs (and bills) zero times in the off-season.
resource "aws_scheduler_schedule" "nfl_ingest" {
  name        = "${var.project}-nfl-ingest"
  description = "NFL Data Ingest Schedule (Aug-Feb, Mon/Fri 00:00 UTC)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 0 ? 8-12,1-2 MON,FRI *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_ingest.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
