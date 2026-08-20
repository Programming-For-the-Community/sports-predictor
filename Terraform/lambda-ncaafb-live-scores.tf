# NCAAFB live-score cache Lambda -- refreshed by EventBridge Scheduler and
# read via API Gateway. Calls ESPN's supplemental college-football
# scoreboard client. Uses its own per-sport role (iam-ncaafb-live-scores.tf).
#
# Zip-packaged, not VPC-attached. Code is deployed by the
# ncaafb_live_scores_deploy workflow, not by Terraform.

resource "aws_cloudwatch_log_group" "ncaafb_live_scores" {
  name              = "/aws/lambda/${var.project}-ncaafb-live-scores"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

data "archive_file" "ncaafb_live_scores_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaafb-live-scores-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaafb_live_scores_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaafb_live_scores" {
  function_name = "${var.project}-ncaafb-live-scores"
  description   = "Refreshes and serves a short-lived live-score cache for NCAAFB events near/at kickoff, via ESPN's supplemental scoreboard client. Triggered by EventBridge Scheduler (refresh) and API Gateway (GET /ncaafb/live-scores)."
  role          = aws_iam_role.ncaafb_live_scores.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # Worst case is a per-event boxscore fetch for every currently-live
  # event, parallelized; a busy NCAAFB Saturday can have many games live
  # at once.
  timeout     = 45
  memory_size = 256

  filename         = data.archive_file.ncaafb_live_scores_placeholder.output_path
  source_code_hash = data.archive_file.ncaafb_live_scores_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
      # FeatureStorage's constructor requires all four of these regardless
      # of which methods actually get called.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaafb_live_scores.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

resource "aws_lambda_function_event_invoke_config" "ncaafb_live_scores" {
  function_name = aws_lambda_function.ncaafb_live_scores.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "api_gateway_invoke_ncaafb_live_scores" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ncaafb_live_scores.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
