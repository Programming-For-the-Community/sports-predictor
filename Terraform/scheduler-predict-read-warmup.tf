# Keeps the 3 predict-read Lambdas warm -- unlike nfl/ncaafb/nba_predict
# and *_live_scores (already invoked on a schedule regardless of real
# traffic, see scheduler-*-live-scores.tf/scheduler-*-season-projection.tf),
# predict-read has no scheduled invocation of its own -- only real API
# Gateway requests reach it. This project's traffic is low enough that its
# execution environments go idle and get reclaimed between visits, so a
# user opening the site after a gap pays full cold start (container init +
# handler import + boto3 client construction) on every one of GET /events,
# /models, /season simultaneously. A ping every 5 minutes -- comfortably
# inside Lambda's idle-reclaim window -- keeps at least one warm container
# per sport so a real visit almost never lands on a cold one.
#
# Each handler's lambda_handler checks event["warmup"] before its normal
# resource-based routing (same distinguishing mechanism the *_predict
# Lambdas use for their own ScheduledSeasonProjection/LiveScoreRefresh
# pings) and constructs its lazy singletons (FeatureStorage, S3Manager,
# the predictions DynamoDBTable) without touching DynamoDB/S3 itself, so
# each tick costs one Lambda invocation and nothing else.
locals {
  predict_read_functions = {
    nfl    = aws_lambda_function.nfl_predict_read.arn
    ncaafb = aws_lambda_function.ncaafb_predict_read.arn
    nba    = aws_lambda_function.nba_predict_read.arn
  }
}

resource "aws_scheduler_schedule" "predict_read_warmup" {
  for_each = local.predict_read_functions

  name        = "${var.project}-${each.key}-predict-read-warmup"
  description = "Pings the ${each.key}-predict-read Lambda every 5 minutes to keep a warm execution environment ready for real requests."
  group_name  = aws_scheduler_schedule_group.sports_predictor.name

  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value
    role_arn = aws_iam_role.eventbridge_invoke.arn

    input = jsonencode({
      warmup = true
    })
  }
}
