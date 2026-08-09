# NCAAFB schedule-sync Lambda. Triggered directly by EventBridge Scheduler
# once its own scheduler-ncaafb-schedule-sync.tf exists. Calls CFBD's
# per-week /games endpoint across the current season in one invocation and
# writes each week's results to S3 -- normalize's S3 trigger
# (s3-raw-data-lake-notifications.tf) picks these up the same way daily
# ingest's output does. Same role as schedule-sync mirrors lambda-nfl-
# schedule-sync.tf's own reasoning: one call per week is a strict subset of
# what ingest/normalize already need.
#
# Code is deployed by the ncaafb_data_pipeline workflow (via `aws lambda
# update-function-code`) -- NOT by Terraform. Same placeholder-ZIP +
# lifecycle.ignore_changes pattern as lambda-nfl-schedule-sync.tf.
#
# Uses the ingest key field, not the backfill key -- this runs against
# production ingest's own call budget, same key as lambda-ncaafb-ingest.tf.

resource "aws_cloudwatch_log_group" "ncaafb_schedule_sync" {
  name              = "/aws/lambda/${var.project}-ncaafb-schedule-sync"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

data "archive_file" "ncaafb_schedule_sync_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaafb-schedule-sync-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaafb_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaafb_schedule_sync" {
  function_name = "${var.project}-ncaafb-schedule-sync"
  description   = "Walks every week of the current NCAAFB season via CFBD and writes each week's results to S3. Triggered by EventBridge Scheduler -- see scheduler-ncaafb-schedule-sync.tf."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # CFBD's bulk per-week endpoint means far fewer calls than NFL's own
  # per-week-scoreboard walk needs (one call per week vs. NFL's per-game
  # calls) -- same generous ceiling as lambda-nfl-schedule-sync.tf anyway,
  # since a season's worth of weeks plus retry/backoff still comfortably
  # fits.
  timeout     = 600
  memory_size = 256

  filename         = data.archive_file.ncaafb_schedule_sync_placeholder.output_path
  source_code_hash = data.archive_file.ncaafb_schedule_sync_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME           = aws_s3_bucket.raw_data_lake.bucket
      CFBD_API_ROOT_URL         = var.cfbd_api_root_url
      CFBD_API_KEY_SECRET_ARN   = var.third_party_api_key_secret_arn
      CFBD_API_KEY_SECRET_FIELD = "ncaa_fb_ingest_key"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaafb_schedule_sync.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}
