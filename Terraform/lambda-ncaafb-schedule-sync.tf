# NCAAFB schedule-sync Lambda. Triggered directly by EventBridge Scheduler
# (scheduler-ncaafb-schedule-sync.tf). Calls CFBD's per-week /games endpoint
# across the current season in one invocation and writes each week's
# results to S3; normalize's S3 trigger
# (s3-raw-data-lake-notifications.tf) picks these up the same way daily
# ingest's output does.
#
# Code is deployed by the ncaafb_data_pipeline workflow (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.
#
# Uses the ingest key field, not the backfill key -- runs against
# production ingest's own call budget.

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
  # A season's worth of weekly CFBD calls plus retry/backoff comfortably
  # fits within this ceiling.
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
