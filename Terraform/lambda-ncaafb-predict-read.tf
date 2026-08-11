# NCAAFB read-only serving Lambda -- GET /ncaafb/events, GET
# /ncaafb/models, GET /ncaafb/season, and (as of the prediction cache)
# the two prediction routes too. Mirrors lambda-nfl-predict-read.tf
# exactly: zip-packaged (no ML dependency footprint), reuses
# aws_iam_role.lambda_inference, same VPC attachment as the main predict
# Lambda. The prediction routes now only ever read/write a cache
# (library.storage.prediction_cache) and, on a miss, fire an async
# invoke of the predict Lambda, which is what actually computes -- see
# predict-read/handler.py's own docstring.
#
# Code is deployed by the ncaafb_deploy workflow -- NOT by Terraform. Same
# placeholder-ZIP + lifecycle.ignore_changes pattern as
# lambda-nfl-predict-read.tf.

resource "aws_cloudwatch_log_group" "ncaafb_predict_read" {
  name              = "/aws/lambda/${var.project}-ncaafb-predict-read"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

data "archive_file" "ncaafb_predict_read_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaafb-predict-read-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaafb_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaafb_predict_read" {
  function_name = "${var.project}-ncaafb-predict-read"
  description   = "Serves GET /ncaafb/events, /ncaafb/models, /ncaafb/season, and the two prediction routes (cache-backed, async-populate-on-miss) -- read-only, no ML model loading. Triggered by API Gateway -- see api-gateway-ncaafb-predict.tf."
  role          = aws_iam_role.lambda_inference.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 29
  memory_size   = 512

  filename         = data.archive_file.ncaafb_predict_read_placeholder.output_path
  source_code_hash = data.archive_file.ncaafb_predict_read_placeholder.output_base64sha256

  environment {
    variables = {
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      PREDICTIONS_TABLE_NAME      = aws_dynamodb_table.predictions.name
      # FeatureStorage's constructor requires all four of these regardless
      # of which methods actually get called -- same rationale as
      # lambda-nfl-predict-read.tf's own comment.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
      # library.aws.lambda_invoker.LambdaInvoker's own target -- fired
      # async (fire-and-forget) on a prediction-cache miss/stale-refresh,
      # see handler.py's own docstring.
      PREDICT_FUNCTION_NAME = aws_lambda_function.ncaafb_predict.function_name
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id, aws_subnet.private_c.id]
    security_group_ids = [aws_security_group.lambda_inference.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaafb_predict_read.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_ncaafb_predict_read" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ncaafb_predict_read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
