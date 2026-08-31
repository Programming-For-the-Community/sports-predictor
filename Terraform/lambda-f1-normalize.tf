# F1 normalize Lambda. Triggered by S3 PutObject events on the raw data
# lake, filtered to the f1/ prefix so only F1 raw files invoke it. Reads
# raw Jolpica-F1 JSON written by f1-ingest from S3, normalizes it into the
# project schema, and upserts entities/events. No Jolpica calls of its own
# (transforms JSON already fetched by ingest), so it needs no extra IAM
# beyond the shared lambda_pipeline role.
#
# Three prefixes reach DynamoDB (results/qualifying/sprint); pitstops/
# standings stay raw-only -- see Source/aws-lambdas/f1/normalize/
# handler.py's own docstring for the full results<->qualifying merge
# mechanics, which PGA's own single-leaderboard-fetch normalize Lambda has
# no equivalent of. Never writes player_game_stats/team_game_stats -- a
# field-event sport's results already live entirely in events.participants
# (design/DATA_SCHEMA.md) -- but PipelineStorage's constructor still
# requires both table name env vars regardless, same as every other
# sport's normalize Lambda.
#
# Code is deployed by the f1_data_pipeline GitHub Actions workflow, not by
# Terraform.

resource "aws_cloudwatch_log_group" "f1_normalize" {
  name              = "/aws/lambda/${var.project}-f1-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

data "archive_file" "f1_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/f1-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via f1_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "f1_normalize" {
  function_name = "${var.project}-f1-normalize"
  description   = "Reads raw Jolpica-F1 results/qualifying/sprint JSON written by f1-ingest and upserts it into the entities and events DynamoDB tables. Triggered by S3 ObjectCreated notifications on the f1/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # A race's own field tops out around 20-26 drivers -- comfortably
  # smaller than PGA's own uncut 150+-competitor field, but left at the
  # same 300s/1024MB budget every other sport's normalize Lambda uses
  # rather than guessing a smaller size would still be safe.
  timeout     = 300
  memory_size = 1024

  filename         = data.archive_file.f1_normalize_placeholder.output_path
  source_code_hash = data.archive_file.f1_normalize_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME     = aws_s3_bucket.raw_data_lake.bucket
      ENTITIES_TABLE_NAME = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME   = aws_dynamodb_table.events.name
      # PipelineStorage's constructor requires all four table names
      # regardless of which one a given invocation actually writes to --
      # F1's own code never calls either of these two (see this file's
      # own header comment).
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.f1_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "f1_normalize" {
  function_name = aws_lambda_function.f1_normalize.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_f1_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.f1_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

# The bucket's S3 event notification config itself lives in
# s3-raw-data-lake-notifications.tf; a bucket can only have one
# aws_s3_bucket_notification resource.
