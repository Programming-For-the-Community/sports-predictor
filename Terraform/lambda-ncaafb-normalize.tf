# NCAAFB normalize Lambda. Triggered by S3 PutObject events on the raw data
# lake, filtered to the ncaafb/ prefix so only NCAAFB raw files invoke it.
# Reads the raw CFBD JSON written by ncaafb-ingest from S3, maps it to the
# project schema, and upserts entities/events/player_game_stats/
# team_game_stats -- same shape as lambda-nfl-normalize.tf. No CFBD calls of
# its own (transforms JSON already fetched by ingest), so it needs no
# secretsmanager grant even though it shares a role with ingest, which does.
#
# Code is deployed by the ncaafb_data_pipeline GitHub Actions workflow --
# NOT by Terraform. Same placeholder-ZIP + lifecycle.ignore_changes pattern
# as lambda-nfl-normalize.tf.

resource "aws_cloudwatch_log_group" "ncaafb_normalize" {
  name              = "/aws/lambda/${var.project}-ncaafb-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

data "archive_file" "ncaafb_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaafb-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaafb_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaafb_normalize" {
  function_name = "${var.project}-ncaafb-normalize"
  description   = "Reads raw CFBD JSON written by ncaafb-ingest and upserts it into the entities, events, and player_game_stats DynamoDB tables. Triggered by S3 ObjectCreated notifications on the ncaafb/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.ncaafb_normalize_placeholder.output_path
  source_code_hash = data.archive_file.ncaafb_normalize_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME              = aws_s3_bucket.raw_data_lake.bucket
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      # PipelineStorage's constructor requires all four table names
      # regardless of which one a given invocation actually writes to --
      # same rationale as lambda-nfl-normalize.tf's own comment.
      TEAM_GAME_STATS_TABLE_NAME = aws_dynamodb_table.team_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaafb_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "ncaafb_normalize" {
  function_name = aws_lambda_function.ncaafb_normalize.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_ncaafb_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ncaafb_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

# The bucket's S3 event notification config itself lives in
# s3-raw-data-lake-notifications.tf -- see lambda-nfl-normalize.tf's own
# comment for why this can't be a second aws_s3_bucket_notification here.
