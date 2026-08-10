# EventBridge Scheduler resource that directly invokes the ncaafb_predict
# Lambda to (re)compute the season projection (standings + bowl/playoff/
# national-championship probabilities + per-stat player prop
# leaderboards) and write it to S3 -- see that Lambda's own docstring and
# the ScheduledSeasonProjection branch in its lambda_handler
# (Source/aws-lambdas/ncaafb/predict/handler.py). GET /ncaafb/season
# serves the cached result instead of computing it live. Mirrors
# scheduler-nfl-season-projection.tf's own shape and reasoning.
#
# Direct Scheduler -> Lambda invoke, same as NFL's own -- one computation,
# no fan-out to justify a Step Functions state machine.
#
# Weekly, Thursday 14:00 UTC, year-round -- an off-season snapshot is
# still cheap and harmless (simulate_season just runs the current record
# forward with zero remaining games). Deliberately NOT timed relative to
# a weekly retrain the way NFL's own Wednesday slot is (2 hours after
# NFL's own weekly training run) -- NCAAFB training is monthly, not
# weekly (scheduler-training-orchestrator.tf), so there's no freshly-
# promoted-model timing to chase here. Thursday, not Wednesday, purely to
# spread load away from NFL's own weekly compute rather than for any real
# dependency.
resource "aws_scheduler_schedule" "ncaafb_season_projection" {
  name        = "${var.project}-ncaafb-season-projection"
  description = "Invokes the ncaafb_predict Lambda weekly, Thu 14:00 UTC, to recompute the season projection and cache it to S3 for GET /ncaafb/season."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression          = "cron(0 14 ? * THU *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ncaafb_predict.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn

    # Doesn't match any API Gateway proxy event shape (no "resource" key)
    # -- lambda_handler checks detail-type before its normal resource-
    # based routing, same distinguishing mechanism NFL's own scheduled
    # invoke uses.
    input = jsonencode({
      detail-type = "ScheduledSeasonProjection"
    })
  }
}
