# PGA live leaderboard-snapshot cache Lambda, triggered by both a
# scheduler refresh and API Gateway reads, calling
# PGAClient.get_leaderboard directly by event_id.
#
# Uses its own per-sport role (iam-pga-live-scores.tf), scoped to PGA's
# own raw-bucket cache prefix.
#
# Zip-packaged, not VPC-attached. Code is deployed by the
# pga_live_scores_deploy workflow, not by Terraform.

resource "aws_cloudwatch_log_group" "pga_live_scores" {
  name              = "/aws/lambda/${var.project}-pga-live-scores"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "serving"
  })
}

data "archive_file" "pga_live_scores_placeholder" {
  type        = "zip"
  output_path = "${path.module}/pga-live-scores-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via pga_live_scores_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "pga_live_scores" {
  function_name = "${var.project}-pga-live-scores"
  description   = "Refreshes and serves a short-lived leaderboard-snapshot cache for PGA tournaments during their poll window, via PGAClient.get_leaderboard. Triggered by EventBridge Scheduler (refresh) and API Gateway (GET /pga/live-scores)."
  role          = aws_iam_role.pga_live_scores.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 60s -- a full ~150-golfer leaderboard response across 1-3 sequential
  # candidates (an overlapping opposite-field week), no per-event fan-out
  # the way NBA's boxscore fetch needs.
  timeout     = 60
  memory_size = 256

  filename         = data.archive_file.pga_live_scores_placeholder.output_path
  source_code_hash = data.archive_file.pga_live_scores_placeholder.output_base64sha256

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
    log_group  = aws_cloudwatch_log_group.pga_live_scores.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "serving"
  })
}

resource "aws_lambda_function_event_invoke_config" "pga_live_scores" {
  function_name = aws_lambda_function.pga_live_scores.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "api_gateway_invoke_pga_live_scores" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pga_live_scores.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
