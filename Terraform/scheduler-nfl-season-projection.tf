# EventBridge Scheduler resource that directly invokes the nfl_predict
# Lambda to (re)compute the season projection (standings + per-stat player
# prop leaderboards) and write it to S3 -- see that Lambda's own docstring
# and the ScheduledSeasonProjection branch in its lambda_handler
# (Source/aws-lambdas/nfl/predict/handler.py). GET /nfl/season serves the
# cached result instead of computing it live (26-29s against API
# Gateway's hard 29s integration ceiling).
#
# Direct Scheduler -> Lambda invoke, NOT routed through a Step Functions
# state machine like the ingest/training orchestrators (sfn-ingest-
# orchestrator.tf, sfn-training-orchestrator.tf) -- those exist
# specifically to fan out per-sport/per-target (see
# iam-stepfunctions-orchestrator.tf's own docstring). This is one
# computation, no fan-out to justify that extra layer.
#
# Weekly, Wednesday 14:00 UTC -- 2 hours after the training-orchestrator
# (scheduler-training-orchestrator.tf, Wed 12:00 UTC) so that week's
# leaderboards use that week's freshly promoted models, not last week's.
# Standings/Elo inputs come from the daily ingest regardless of this
# schedule's own timing.
resource "aws_scheduler_schedule" "nfl_season_projection" {
  name        = "${var.project}-nfl-season-projection"
  description = "Invokes the nfl_predict Lambda weekly, Wed 14:00 UTC, to recompute the season projection and cache it to S3 for GET /nfl/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    # Doesn't match any API Gateway proxy event shape (no "resource" key)
    # -- lambda_handler checks detail-type before its normal resource-
    # based routing, exactly the way it distinguishes this invocation from
    # a real HTTP request.
    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
