# NFL read-only serving Lambda -- GET /nfl/events, /nfl/models,
# /nfl/season, and the two prediction routes (cache read-through, see
# Source/aws-lambdas/nfl/predict-read/handler.py). Separate from the
# main predict Lambda because none of these load an ML model artifact,
# so this needs nothing beyond boto3 + the shared library package.
# Zip-packaged, not a container image.
#
# Code is deployed by the nfl_deploy workflow (via `aws lambda
# update-function-code`) -- NOT by Terraform. The placeholder ZIP below
# satisfies Terraform's requirement that a Lambda function have code at
# creation time. lifecycle.ignore_changes ensures a subsequent
# `terraform apply` never reverts CI-deployed code back to the placeholder.
#
# Reuses aws_iam_role.lambda_inference (iam-lambda-inference.tf) rather
# than a new scoped-down role -- everything this Lambda needs (S3 read on
# model artifacts, DynamoDB read on events/predictions) is already a
# strict subset of what that role already grants the main predict Lambda,
# so a second role would add IAM surface without tightening anything real.
#
# VPC-attached, same subnets/security group as the main predict Lambda --
# DynamoDB/S3 are only reachable via the VPC Gateway Endpoints
# (vpc-endpoints.tf) regardless of which Lambda is asking, so this doesn't
# save anything on that front. The win here is entirely about what gets
# imported at module load time, not about network path.

resource "aws_cloudwatch_log_group" "nfl_predict_read" {
  name              = "/aws/lambda/${var.project}-nfl-predict-read"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

data "archive_file" "nfl_predict_read_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nfl-predict-read-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nfl_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nfl_predict_read" {
  function_name = "${var.project}-nfl-predict-read"
  description   = "Serves GET /nfl/events, /nfl/models, /nfl/season, and the two prediction routes (cache-backed, async-populate-on-miss) -- read-only, no ML model loading. Triggered by API Gateway -- see api-gateway-nfl-predict.tf."
  role          = aws_iam_role.lambda_inference.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # Same API Gateway REST API integration ceiling as the main predict
  # Lambda, though this one should never come close -- no ML inference,
  # just a handful of DynamoDB/S3 reads.
  timeout     = 29
  memory_size = 512

  filename         = data.archive_file.nfl_predict_read_placeholder.output_path
  source_code_hash = data.archive_file.nfl_predict_read_placeholder.output_base64sha256

  environment {
    variables = {
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      PREDICTIONS_TABLE_NAME      = aws_dynamodb_table.predictions.name
      # FeatureStorage's constructor requires all four of these regardless
      # of which of its methods actually get called -- list_events only
      # ever calls get_all_events, but FeatureStorage() itself would raise
      # at construction time without the other three set too.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
      PREDICT_FUNCTION_NAME        = aws_lambda_function.nfl_predict.function_name # LambdaInvoker's async-invoke target
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id, aws_subnet.private_c.id]
    security_group_ids = [aws_security_group.lambda_inference.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nfl_predict_read.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_nfl_predict_read" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nfl_predict_read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
