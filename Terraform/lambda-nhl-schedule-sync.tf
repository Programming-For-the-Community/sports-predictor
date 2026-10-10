# NHL schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
# see scheduler-nhl-schedule-sync.tf. Walks up to
# handler.py's own SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar days via
# ESPN's scoreboard-by-date endpoint and writes each date's results to S3;
# normalize's existing S3 trigger (s3-raw-data-lake-notifications.tf)
# picks these up the same way daily ingest's output does.
#
# Full-season walk with an idempotent skip-if-already-synced check per
# date; the nearest two weeks are re-fetched every run, which is also
# what keeps each game's probable starting goalies current.
#
# Code is deployed by the data_pipeline workflow (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.
#
# Reuses aws_iam_role.lambda_pipeline rather than a new role.

resource "aws_cloudwatch_log_group" "nhl_schedule_sync" {
  name              = "/aws/lambda/${var.project}-nhl-schedule-sync"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nhl"
    Component = "ingestion"
  })
}

data "archive_file" "nhl_schedule_sync_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nhl-schedule-sync-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nhl_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nhl_schedule_sync" {
  function_name = "${var.project}-nhl-schedule-sync"
  description   = "Seeds the rest of the NHL season's scoreboards from ESPN (idempotent, skip-if-already-synced) so remaining_games always has data ahead of daily ingest. Triggered by EventBridge Scheduler."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 270-day ceiling (handler.py's own SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS) at
  # the shared RateLimiter's 0.3s floor can approach 120s on a full,
  # not-yet-synced walk; 300s gives headroom.
  timeout     = 300
  memory_size = 256

  filename         = data.archive_file.nhl_schedule_sync_placeholder.output_path
  source_code_hash = data.archive_file.nhl_schedule_sync_placeholder.output_base64sha256

  environment {
    variables = {
      AWS_ACCOUNT_ID    = var.account_id
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nhl_schedule_sync.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nhl"
    Component = "ingestion"
  })
}
