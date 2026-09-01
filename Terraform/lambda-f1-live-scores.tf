# F1 live-score cache Lambda, triggered by both a scheduler refresh and
# API Gateway reads, calling F1EspnClient.get_scoreboard directly.
# Genuinely ESPN-sourced, unlike every other F1 Lambda (Jolpica-sourced)
# -- see aws-lambdas/f1/live-scores/live_scores.py's own docstring for why.
#
# Uses its own per-sport role (iam-f1-live-scores.tf), scoped to F1's own
# raw-bucket cache prefix.
#
# Zip-packaged, not VPC-attached. Code is deployed by the
# f1_live_scores_deploy workflow, not by Terraform.

resource "aws_cloudwatch_log_group" "f1_live_scores" {
  name              = "/aws/lambda/${var.project}-f1-live-scores"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "serving"
  })
}

data "archive_file" "f1_live_scores_placeholder" {
  type        = "zip"
  output_path = "${path.module}/f1-live-scores-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via f1_live_scores_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "f1_live_scores" {
  function_name = "${var.project}-f1-live-scores"
  description   = "Refreshes and serves a short-lived live-timing cache for F1 races, via F1EspnClient.get_scoreboard. Triggered by EventBridge Scheduler (refresh) and API Gateway (GET /f1/live-scores)."
  role          = aws_iam_role.f1_live_scores.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 60s -- one full-season scoreboard fetch (~25 events x up to 7
  # competitions each) plus a small (~20-driver) roster resolution, no
  # per-event fan-out.
  timeout     = 60
  memory_size = 256

  filename         = data.archive_file.f1_live_scores_placeholder.output_path
  source_code_hash = data.archive_file.f1_live_scores_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
      # FeatureStorage's constructor requires all four of these regardless
      # of which methods actually get called; live_scores.py only ever
      # queries the events table.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.f1_live_scores.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "serving"
  })
}

resource "aws_lambda_function_event_invoke_config" "f1_live_scores" {
  function_name = aws_lambda_function.f1_live_scores.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "api_gateway_invoke_f1_live_scores" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.f1_live_scores.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
