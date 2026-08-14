# NBA normalize Lambda. Triggered by S3 PutObject events on the raw data
# lake, filtered to the nba/ prefix so only NBA raw files invoke it. Reads
# the raw ESPN JSON written by nba-ingest from S3, maps it to the project
# schema (splitting ESPN's combined "made-attempted" stat strings -- see
# project memory's 2026-08-13 live-verification notes), and upserts
# entities/events/player_game_stats/team_game_stats -- same shape as
# lambda-ncaafb-normalize.tf. No ESPN calls of its own (transforms JSON
# already fetched by ingest), so it needs no extra IAM beyond the shared
# lambda_pipeline role.
#
# Code is deployed by the nba_data_pipeline GitHub Actions workflow -- NOT
# by Terraform. Same placeholder-ZIP + lifecycle.ignore_changes pattern as
# lambda-ncaafb-normalize.tf.

resource "aws_cloudwatch_log_group" "nba_normalize" {
  name              = "/aws/lambda/${var.project}-nba-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "ingestion"
  })
}

data "archive_file" "nba_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nba-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nba_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nba_normalize" {
  function_name = "${var.project}-nba-normalize"
  description   = "Reads raw ESPN JSON written by nba-ingest and upserts it into the entities, events, player_game_stats, and team_game_stats DynamoDB tables. Triggered by S3 ObjectCreated notifications on the nba/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 300s/1024MB -- generous headroom for a recurring nightly job, but NOT
  # NCAAFB's own thread-pooled tier for the same reason: NCAAFB's threading
  # exists because CFBD writes ONE bulk per-week file (1122 entities
  # measured in a single invocation, most of the way to its 60s timeout).
  # NBA's ingest instead writes one S3 object per game (~26 players) and
  # per team roster (~15-17 players) -- the same small per-invocation
  # volume as NFL's own unthreaded normalize handles today, so a plain
  # serial loop (see handler.py) is the right match here, not a preemptive
  # thread pool sized for a bulk-write shape this sport doesn't have.
  timeout     = 300
  memory_size = 1024

  filename         = data.archive_file.nba_normalize_placeholder.output_path
  source_code_hash = data.archive_file.nba_normalize_placeholder.output_base64sha256

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
    log_group  = aws_cloudwatch_log_group.nba_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "nba_normalize" {
  function_name = aws_lambda_function.nba_normalize.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_nba_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nba_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

# The bucket's S3 event notification config itself lives in
# s3-raw-data-lake-notifications.tf -- see lambda-nfl-normalize.tf's own
# comment for why this can't be a second aws_s3_bucket_notification here.
