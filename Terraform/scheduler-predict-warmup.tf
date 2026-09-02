# Keeps the 6 heavy (container-image, xgboost/pandas/sklearn) predict
# Lambdas warm. Unlike the predict-read Lambdas (scheduler-predict-read-
# warmup.tf, zip-packaged, sub-second cold start), these have a large
# enough import chain that a cold start can exceed Lambda's fixed
# 10-second INIT-phase budget. A ping every 5 minutes, inside Lambda's
# idle-reclaim window, keeps an already-initialized container ready.
#
# Each handler's lambda_handler checks event["warmup"] before its normal
# detail-type routing and constructs its lazy singletons (FeatureStorage,
# S3Manager, the predictions DynamoDBTable) without touching DynamoDB/S3
# itself, so each tick costs one Lambda invocation and nothing else.
# IAM: already covered -- iam-eventbridge-invoke.tf's
# InvokeDirectLambdaJobs statement grants this role invoke on all 6.
locals {
  predict_functions = {
    nfl     = aws_lambda_function.nfl_predict.arn
    ncaafb  = aws_lambda_function.ncaafb_predict.arn
    nba     = aws_lambda_function.nba_predict.arn
    ncaambb = aws_lambda_function.ncaambb_predict.arn
    pga     = aws_lambda_function.pga_predict.arn
    f1      = aws_lambda_function.f1_predict.arn
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
