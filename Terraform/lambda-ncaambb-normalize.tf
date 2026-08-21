# NCAA MBB normalize Lambda. Triggered by S3 PutObject events on the raw
# data lake, filtered to the ncaambb/ prefix so only NCAA MBB raw files
# invoke it. Reads the raw ESPN JSON written by ncaambb-ingest from S3,
# maps it to the project schema (splitting ESPN's combined "made-attempted"
# stat strings), and upserts entities/events/player_game_stats/
# team_game_stats. No ESPN calls of its own (transforms JSON already
# fetched by ingest), so it needs no extra IAM beyond the shared
# lambda_pipeline role.
#
# Code is deployed by the ncaambb_data_pipeline GitHub Actions workflow, not
# by Terraform.

resource "aws_cloudwatch_log_group" "ncaambb_normalize" {
  name              = "/aws/lambda/${var.project}-ncaambb-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

data "archive_file" "ncaambb_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaambb-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaambb_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaambb_normalize" {
  function_name = "${var.project}-ncaambb-normalize"
  description   = "Reads raw ESPN JSON written by ncaambb-ingest and upserts it into the entities, events, player_game_stats, and team_game_stats DynamoDB tables. Triggered by S3 ObjectCreated notifications on the ncaambb/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 300s/1024MB -- same as NBA's. One S3 object per game/roster still
  # means one Lambda invocation per object here (S3 notifications, not a
  # bulk batch this Lambda itself fans out) -- normalize's own per-
  # invocation volume doesn't scale with NCAA MBB's higher games-per-night
  # count the way ingest's own fetch loops do (see
  # aws-lambdas/ncaambb/ingest/handler.py's VOLUME docstring section);
  # more simultaneous S3 PutObject events just means more concurrent
  # normalize invocations, not a bigger single one.
  timeout     = 300
  memory_size = 1024

  filename         = data.archive_file.ncaambb_normalize_placeholder.output_path
  source_code_hash = data.archive_file.ncaambb_normalize_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME              = aws_s3_bucket.raw_data_lake.bucket
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      # PipelineStorage's constructor requires all four table names
      # regardless of which one a given invocation actually writes to.
      TEAM_GAME_STATS_TABLE_NAME = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaambb_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "ncaambb_normalize" {
  function_name = aws_lambda_function.ncaambb_normalize.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_ncaambb_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ncaambb_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

# The bucket's S3 event notification config itself lives in
# s3-raw-data-lake-notifications.tf; a bucket can only have one
# aws_s3_bucket_notification resource.
