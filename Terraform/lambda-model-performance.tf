# Model-performance Lambda. Invoked daily by EventBridge Scheduler
# (scheduler-model-performance.tf). Shared across the head-to-head sports:
# scores every promoted model against the season's completed events and
# writes the scorecard the Performance tab reads -- see
# library/performance/runner.py.
#
# Code is deployed by shared_lambdas_deploy.yml (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.

resource "aws_cloudwatch_log_group" "model_performance" {
  name              = "/aws/lambda/${var.project}-model-performance"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "prediction"
  })
}

data "archive_file" "model_performance_placeholder" {
  type        = "zip"
  output_path = "${path.module}/model-performance-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via shared_lambdas_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "model_performance" {
  function_name = "${var.project}-model-performance"
  description   = "Daily: scores each promoted model against the season's completed events (via pre-kickoff snapshots) and writes the per-sport scorecard served by GET /{sport}/model-performance. See scheduler-model-performance.tf."
  role          = aws_iam_role.lambda_model_performance.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 512

  filename         = data.archive_file.model_performance_placeholder.output_path
  source_code_hash = data.archive_file.model_performance_placeholder.output_base64sha256

  environment {
    variables = {
      AWS_ACCOUNT_ID              = var.account_id
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      PREDICTIONS_TABLE_NAME      = aws_dynamodb_table.predictions.name
      # FeatureStorage's constructor requires all four of these regardless
      # of which of its methods actually get called.
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.model_performance.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "prediction"
  })
}
