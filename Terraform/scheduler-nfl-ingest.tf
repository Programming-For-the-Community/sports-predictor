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

# August through February, every day at 10:00 UTC. One schedule covering
# the whole window (rather than a tighter in-season-only cadence) so a
# season start/end date shifting by a week or two never falls outside it
# -- ESPN having no games on a given day is a no-op fetch, not a problem
# worth optimizing away. No schedule covers March through July, so the
# pipeline runs (and bills) zero times in the off-season.
#
# Daily, not just Tue/Wed, specifically because of injury data: coach
# data and box scores barely change day to day (box scores are already
# skip-if-exists in handler.py, coaches change maybe once a season), but
# injury reports genuinely update through the week as teams file new
# ones -- a Tue/Wed-only cadence would serve a stale injury snapshot for
# most of the week. handler.py's target-week resolution (most recent
# Sunday's date) is the same value every day within a given NFL week, so
# a daily run just re-derives and re-writes that same week's scoreboard
# key with freshly-fetched coach/injury/depth-chart data each time --
# see handler.py's own docstring for why re-embedding everything on every
# run (rather than a lighter "injuries-only" mode) is the design: a
# lighter mode that skipped re-fetching coach/depth-chart data would
# silently wipe those fields on the next normalize rebuild, since
# scoreboard_event_to_event_item rebuilds the whole event item from
# scratch every time -- confirmed as a real bug in an earlier version of
# this design, not just a theoretical concern.
resource "aws_scheduler_schedule" "nfl_ingest" {
  name        = "${var.project}-nfl-ingest"
  description = "NFL Data Ingest Schedule (Aug-Feb, daily 10:00 UTC)"
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 10 ? 8-12,1-2 * *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_ingest.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }
}
