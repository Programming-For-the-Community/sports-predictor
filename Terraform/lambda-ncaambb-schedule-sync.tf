# NCAA MBB schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
# see scheduler-ncaambb-schedule-sync.tf. Walks up to
# handler.py's own SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar days via
# ESPN's scoreboard-by-date endpoint and writes each date's results to S3;
# normalize's existing S3 trigger (s3-raw-data-lake-notifications.tf)
# picks these up the same way daily ingest's output does.
#
# Full-season walk with an idempotent skip-if-already-synced check per
# date -- season_projection.py's remaining_games input needs the whole
# rest of the season seeded.
#
# Code is deployed by the ncaambb_data_pipeline workflow (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.
#
# Reuses aws_iam_role.lambda_pipeline rather than a new role.

resource "aws_cloudwatch_log_group" "ncaambb_schedule_sync" {
  name              = "/aws/lambda/${var.project}-ncaambb-schedule-sync"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

data "archive_file" "ncaambb_schedule_sync_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaambb-schedule-sync-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaambb_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaambb_schedule_sync" {
  function_name = "${var.project}-ncaambb-schedule-sync"
  description   = "Seeds the rest of the NCAA MBB season's scoreboards from ESPN (idempotent, skip-if-already-synced) so remaining_games always has data ahead of daily ingest. Triggered by EventBridge Scheduler."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 270-day ceiling (handler.py's own SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS) at
  # the shared RateLimiter's 0.3s floor can approach 120s on a full,
  # not-yet-synced walk; 300s gives headroom. Same as NBA's own timeout --
  # this Lambda's per-invocation volume is one scoreboard call per date,
  # not per game, so it doesn't scale with NCAA MBB's higher games-per-
  # night count the way ingest's does (see handler.py's own docstring).
  timeout     = 300
  memory_size = 256

  filename         = data.archive_file.ncaambb_schedule_sync_placeholder.output_path
  source_code_hash = data.archive_file.ncaambb_schedule_sync_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaambb_schedule_sync.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}
