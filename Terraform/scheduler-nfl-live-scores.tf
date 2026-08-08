# EventBridge Scheduler resource that directly invokes the nfl_live_scores
# Lambda every 60s to refresh its live-score cache -- see that Lambda's own
# docstring (Source/aws-lambdas/nfl/live-scores/handler.py) and
# live_scores.refresh's docstring for why this almost never actually
# results in an ESPN call: the handler itself checks events already in
# DynamoDB for one within 15 minutes of kickoff (live_scores.
# POLL_START_BEFORE_KICKOFF) before ever reaching out to ESPN, so a tick on
# a day/time with nothing near kickoff costs a single cheap DynamoDB Query
# and nothing else.
#
# Direct Scheduler -> Lambda invoke, same reasoning as
# scheduler-nfl-season-projection.tf -- one computation, no per-sport/
# per-target fan-out to justify routing through a Step Functions
# orchestrator.
resource "aws_scheduler_schedule" "nfl_live_scores" {
  name        = "${var.project}-nfl-live-scores"
  description = "Invokes the nfl_live_scores Lambda every 60s to refresh its live-score cache for any event within 15 minutes of kickoff or still in progress."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(1 minute)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_live_scores.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    # Doesn't match any API Gateway proxy event shape (no "resource" key)
    # -- lambda_handler checks detail-type before its normal resource-
    # based routing, same distinguishing mechanism
    # scheduler-nfl-season-projection.tf's own ScheduledSeasonProjection
    # trigger uses.
    input = jsonencode({
      detail-type = "LiveScoreRefresh"
    })
  }
}
