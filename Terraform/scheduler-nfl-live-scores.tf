# EventBridge Scheduler resource that directly invokes the nfl_live_scores
# Lambda every 60s to refresh its live-score cache. The handler checks
# events already in DynamoDB for one within 15 minutes of kickoff
# (live_scores.POLL_START_BEFORE_KICKOFF) before reaching out to ESPN, so a
# tick with nothing near kickoff costs a single cheap DynamoDB Query.
#
# Direct Scheduler -> Lambda invoke: one computation, no per-sport/
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
    # based routing.
    input = jsonencode({
      detail-type = "LiveScoreRefresh"
    })
  }
}
