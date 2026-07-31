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

# August through February, Tuesdays and Wednesdays at 10:00 UTC. One
# schedule covering the whole window (rather than a tighter in-season-only
# cadence) so a season start/end date shifting by a week or two never
# falls outside it -- ESPN having no games on a given day is a no-op
# fetch, not a problem worth optimizing away. No schedule covers March
# through July, so the pipeline runs (and bills) zero times in the
# off-season.
#
# Tuesday is the earliest day a full NFL week (including Monday Night
# Football, which typically wraps ~04:00-05:00 UTC Tuesday) is guaranteed
# complete, with comfortable margin past that. Wednesday is a retry for
# anything ESPN hadn't finalized as of Tuesday, and it's also what the
# feature-engineering schedule (scheduler-nfl-feature-engineering.tf,
# later the same Wednesday) depends on having already run.
#
# Both runs can safely share one schedule/time now -- the handler resolves
# its target week from the most recent Sunday's date (see handler.py's
# module docstring), which is identical on Tuesday and Wednesday within
# the same NFL week. An earlier version of this schedule had Tuesday and
# Wednesday as two separate resources at different times, to dodge an
# undocumented ESPN day-of-week rollover the handler used to depend on --
# that dependency is gone, so the single combined schedule is correct
# again, not just simpler.
resource "aws_scheduler_schedule" "nfl_ingest" {
  name        = "${var.project}-nfl-ingest"
  description = "NFL Data Ingest Schedule (Aug-Feb, Tue/Wed 10:00 UTC)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? 8-12,1-2 TUE,WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_ingest.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
