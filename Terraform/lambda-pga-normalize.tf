# PGA normalize Lambda. Triggered by S3 PutObject events on the raw data
# lake, filtered to the pga/ prefix so only PGA raw files invoke it. Reads
# the raw ESPN leaderboard JSON written by pga-ingest/pga-schedule-sync
# from S3, maps it to the project schema, and upserts entities/events. No
# ESPN calls of its own (transforms JSON already fetched by ingest), so it
# needs no extra IAM beyond the shared lambda_pipeline role.
#
# Only one raw payload shape exists for PGA (pga/leaderboard/{season}/
# {event_id}.json -- see Source/aws-lambdas/pga/normalize/handler.py's own
# docstring), unlike every head-to-head sport's normalize Lambda which
# routes several distinct S3 key patterns. Never writes
# player_game_stats/team_game_stats -- a field-event sport's results
# already live entirely in events.participants (design/DATA_SCHEMA.md) --
# but PipelineStorage's constructor still requires both table name env
# vars regardless of whether a given sport's normalize code ever calls
# write_player_game_stats/write_team_game_stats, same as every other
# sport's normalize Lambda.
#
# Code is deployed by the pga_data_pipeline GitHub Actions workflow, not
# by Terraform.

resource "aws_cloudwatch_log_group" "pga_normalize" {
  name              = "/aws/lambda/${var.project}-pga-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

data "archive_file" "pga_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/pga-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via pga_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "pga_normalize" {
  function_name = "${var.project}-pga-normalize"
  description   = "Reads raw ESPN leaderboard JSON written by pga-ingest/pga-schedule-sync and upserts it into the entities and events DynamoDB tables. Triggered by S3 ObjectCreated notifications on the pga/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # A single tournament's leaderboard tops out around 150-160 competitors
  # (a full, uncut field) -- comfortably inside the same 300s/1024MB
  # budget every other sport's normalize Lambda uses.
  timeout     = 300
  memory_size = 1024

  filename         = data.archive_file.pga_normalize_placeholder.output_path
  source_code_hash = data.archive_file.pga_normalize_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME     = aws_s3_bucket.raw_data_lake.bucket
      ENTITIES_TABLE_NAME = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME   = aws_dynamodb_table.events.name
      # PipelineStorage's constructor requires all four table names
      # regardless of which one a given invocation actually writes to --
      # PGA's own code never calls either of these two (see this file's
      # own header comment).
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.pga_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "pga_normalize" {
  function_name = aws_lambda_function.pga_normalize.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_pga_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pga_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

# The bucket's S3 event notification config itself lives in
# s3-raw-data-lake-notifications.tf; a bucket can only have one
# aws_s3_bucket_notification resource.
