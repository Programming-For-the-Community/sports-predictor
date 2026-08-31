# Keeps the 6 predict-read Lambdas warm. Only real API Gateway requests
# reach predict-read, so a ping every 5 minutes, comfortably inside
# Lambda's idle-reclaim window, keeps at least one warm container per
# sport ready for real visits.
#
# Each handler's lambda_handler checks event["warmup"] before its normal
# resource-based routing, and constructs its lazy singletons
# (FeatureStorage, S3Manager, the predictions DynamoDBTable) without
# touching DynamoDB/S3 itself, so each tick costs one Lambda invocation
# and nothing else.
locals {
  predict_read_functions = {
    nfl     = aws_lambda_function.nfl_predict_read.arn
    ncaafb  = aws_lambda_function.ncaafb_predict_read.arn
    nba     = aws_lambda_function.nba_predict_read.arn
    ncaambb = aws_lambda_function.ncaambb_predict_read.arn
    pga     = aws_lambda_function.pga_predict_read.arn
    f1      = aws_lambda_function.f1_predict_read.arn
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
