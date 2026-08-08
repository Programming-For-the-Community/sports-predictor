# NFL live-score cache Lambda. Two triggers, one function -- see
# Source/aws-lambdas/nfl/live-scores/handler.py's own docstring:
#   - scheduler-nfl-live-scores.tf invokes it every 60s to refresh the
#     cache (LiveScoreRefresh detail-type).
#   - api-gateway-nfl-live-scores.tf routes GET /nfl/live-scores to it.
#
# Zip-packaged like ingest/normalize/predict-read, not a container image --
# no ML dependency footprint here at all (see handler.py's own docstring
# for why this isn't just folded into ingest or predict-read). Code is
# deployed by the nfl_live_scores_deploy GitHub Actions workflow (via `aws
# lambda update-function-code`) -- NOT by Terraform. The placeholder ZIP
# below satisfies Terraform's requirement that a Lambda function have code
# at creation time. lifecycle.ignore_changes ensures a subsequent
# `terraform apply` never reverts CI-deployed code back to the placeholder.
#
# Not VPC-attached -- needs direct internet access to reach ESPN, same as
# ingest/schedule-sync.

resource "aws_cloudwatch_log_group" "nfl_live_scores" {
  name              = "/aws/lambda/${var.project}-nfl-live-scores"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

data "archive_file" "nfl_live_scores_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nfl-live-scores-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nfl_live_scores_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nfl_live_scores" {
  function_name = "${var.project}-nfl-live-scores"
  description   = "Refreshes and serves a short-lived live-score cache for events near/at kickoff. Triggered by EventBridge Scheduler (refresh) and API Gateway (GET /nfl/live-scores) -- see scheduler-nfl-live-scores.tf and api-gateway-nfl-live-scores.tf."
  role          = aws_iam_role.lambda_live_scores.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # A single ESPN scoreboard call plus a DynamoDB Query on the rare tick
  # that finds a candidate -- generous headroom over the common case
  # (zero ESPN calls, near-instant) without approaching API Gateway's own
  # 29s integration ceiling on the read path.
  timeout     = 20
  memory_size = 256

  filename         = data.archive_file.nfl_live_scores_placeholder.output_path
  source_code_hash = data.archive_file.nfl_live_scores_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      # FeatureStorage's constructor requires all four of these regardless
      # of which methods actually get called (see iam-lambda-live-scores.tf's
      # own comment) -- live_scores.py only ever queries the events table.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nfl_live_scores.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

resource "aws_lambda_function_event_invoke_config" "nfl_live_scores" {
  function_name = aws_lambda_function.nfl_live_scores.function_name

  # Same reasoning as lambda-nfl-ingest.tf's own event-invoke config --
  # explicit retry/age bounds for the async (EventBridge) trigger path so
  # a stale refresh tick doesn't queue indefinitely. Irrelevant to the
  # synchronous API Gateway trigger path.
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "api_gateway_invoke_nfl_live_scores" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nfl_live_scores.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
