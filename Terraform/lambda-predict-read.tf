# Shared read-only serving Lambda for all 6 sports -- GET /{sport}/events,
# /{sport}/models, /{sport}/season, and the prediction routes (cache
# read-through), for every sport at once. Replaces the 6 former per-sport
# predict-read Lambdas (lambda-{sport}-predict-read.tf, deleted) now that
# each sport's own handler.py had shrunk to almost nothing but config --
# see Source/aws-lambdas/shared/predict-read/handler.py's own docstring.
# Every api-gateway-{sport}-predict.tf's own `/{sport}/...` resources all
# integrate with this ONE function now; API Gateway's resource tree itself
# is unchanged.
#
# Reuses aws_iam_role.lambda_inference (iam-lambda-inference.tf) --
# already a single shared role granting exactly what this needs across
# every sport (S3 read on model artifacts, DynamoDB read/write on the
# already-shared entities/events/predictions tables, invoke on all 6
# predict Lambdas), so no IAM changes were needed to consolidate.
#
# Not VPC-attached -- same reasoning every one of the 6 Lambdas this
# replaces already had: S3/DynamoDB are reachable over their public
# regional endpoints without a VPC, and this Lambda also calls
# lambda:InvokeFunction to trigger a predict Lambda on a cache miss, which
# isn't reachable from inside the VPC (no Lambda Interface Endpoint here).
#
# Code is deployed by shared_lambdas_deploy.yml (via `aws lambda
# update-function-code`), not by Terraform. The placeholder ZIP below
# satisfies Terraform's requirement that a Lambda function have code at
# creation time; lifecycle.ignore_changes keeps `terraform apply` from
# reverting CI-deployed code back to the placeholder.

resource "aws_cloudwatch_log_group" "predict_read" {
  name              = "/aws/lambda/${var.project}-predict-read"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

data "archive_file" "predict_read_placeholder" {
  type        = "zip"
  output_path = "${path.module}/predict-read-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via shared_lambdas_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "predict_read" {
  function_name = "${var.project}-predict-read"
  description   = "Serves GET /{sport}/events, /{sport}/models, /{sport}/season, and the prediction routes (cache-backed, async-populate-on-miss) for all 6 sports -- read-only, no ML model loading. Triggered by API Gateway -- see api-gateway-{sport}-predict.tf."
  role          = aws_iam_role.lambda_inference.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # API Gateway REST API's integration ceiling.
  timeout     = 29
  memory_size = 512

  filename         = data.archive_file.predict_read_placeholder.output_path
  source_code_hash = data.archive_file.predict_read_placeholder.output_base64sha256

  environment {
    variables = {
      # Known at deploy time -- avoids get_account_id() (library/aws/
      # account.py) needing an STS call on every cold start.
      AWS_ACCOUNT_ID              = var.account_id
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      PREDICTIONS_TABLE_NAME      = aws_dynamodb_table.predictions.name
      # FeatureStorage's constructor requires all four of these regardless
      # of which of its methods actually get called.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
      # The one genuinely per-sport piece of config left -- each sport's
      # own cache-miss trigger must async-invoke a DIFFERENT predict
      # Lambda (LambdaInvoker's own async-invoke target).
      NFL_PREDICT_FUNCTION_NAME     = aws_lambda_function.nfl_predict.function_name
      NBA_PREDICT_FUNCTION_NAME     = aws_lambda_function.nba_predict.function_name
      NCAAFB_PREDICT_FUNCTION_NAME  = aws_lambda_function.ncaafb_predict.function_name
      NCAAMBB_PREDICT_FUNCTION_NAME = aws_lambda_function.ncaambb_predict.function_name
      PGA_PREDICT_FUNCTION_NAME     = aws_lambda_function.pga_predict.function_name
      F1_PREDICT_FUNCTION_NAME      = aws_lambda_function.f1_predict.function_name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.predict_read.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_predict_read" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.predict_read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
