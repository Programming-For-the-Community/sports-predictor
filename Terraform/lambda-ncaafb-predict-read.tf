# NCAAFB read-only serving Lambda for GET /ncaafb/events, /ncaafb/models,
# /ncaafb/season, and the two prediction routes (cache read-through).
#
# Not VPC-attached -- S3/DynamoDB are reachable over their public regional
# endpoints without a VPC, and this Lambda also calls lambda:InvokeFunction
# to trigger the predict Lambda on a cache miss, which isn't reachable
# from inside the VPC.
#
# Code is deployed by the ncaafb_deploy workflow, not by Terraform, using a
# placeholder ZIP with lifecycle.ignore_changes.

resource "aws_cloudwatch_log_group" "ncaafb_predict_read" {
  name              = "/aws/lambda/${var.project}-ncaafb-predict-read"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaafb"
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
      # of which methods actually get called.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
      PREDICT_FUNCTION_NAME        = aws_lambda_function.ncaafb_predict.function_name # LambdaInvoker's async-invoke target
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaafb_predict_read.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaafb"
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
