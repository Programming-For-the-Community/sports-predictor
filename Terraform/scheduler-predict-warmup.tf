# Keeps the 4 heavy (container-image, xgboost/pandas/sklearn) predict
# Lambdas warm. Unlike the predict-read Lambdas (scheduler-predict-read-
# warmup.tf, zip-packaged, sub-second cold start), these have a large
# enough import chain that a genuinely cold start regularly exceeds
# Lambda's fixed 10-second INIT-phase budget and gets retried by the
# platform -- confirmed live via CloudWatch Logs (repeated
# "INIT_REPORT ... Status: timeout" entries on nfl/ncaafb/nba; ncaambb
# wired in from day one per its own onboarding plan, not retrofitted
# after a live complaint the way the first 3 sports were). A ping every
# 5 minutes, comfortably inside Lambda's idle-reclaim window, keeps at
# least one already-initialized container ready so a real request doesn't
# pay that cost itself.
#
# Each handler's lambda_handler checks event["warmup"] before its normal
# detail-type routing and constructs its lazy singletons (FeatureStorage,
# S3Manager, the predictions DynamoDBTable) without touching
# DynamoDB/S3 itself, so each tick costs one Lambda invocation and
# nothing else. IAM: already covered -- iam-eventbridge-invoke.tf's
# InvokeDirectLambdaJobs statement already grants this role invoke on
# all 3 of these functions (used by each sport's own scheduled season-
# projection trigger).
locals {
  predict_functions = {
    nfl     = aws_lambda_function.nfl_predict.arn
    ncaafb  = aws_lambda_function.ncaafb_predict.arn
    nba     = aws_lambda_function.nba_predict.arn
    ncaambb = aws_lambda_function.ncaambb_predict.arn
  }
}

resource "aws_scheduler_schedule" "predict_warmup" {
  for_each = local.predict_functions

  name        = "${var.project}-${each.key}-predict-warmup"
  description = "Pings the ${each.key}-predict Lambda every 5 minutes to keep a warm, already-initialized execution environment ready for a real cache-miss compute."
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
