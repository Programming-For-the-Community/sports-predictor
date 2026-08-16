# NBA schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
# see scheduler-nba-schedule-sync.tf. Walks up to
# handler.py's own SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS calendar days via
# ESPN's scoreboard-by-date endpoint and writes each date's results to S3
# -- normalize's existing S3 trigger (s3-raw-data-lake-notifications.tf)
# picks these up the same way daily ingest's output does. Exists for the
# same reason NFL's own schedule-sync does (daily ingest alone never seeds
# future dates ahead of time).
#
# Full-season walk with an idempotent skip-if-already-synced check per
# date (2026-08-16, Sub-phase 3A step 8) -- season_projection.py's
# remaining_games input needs the whole rest of the season seeded, not
# just two weeks. See handler.py's own docstring for the full reasoning
# (previously a fixed 14-day window; that was a real, documented gap left
# open specifically until this step needed fixing it).
#
# Code is deployed by the nba_data_pipeline workflow (via `aws lambda
# update-function-code`) -- NOT by Terraform. Same placeholder-ZIP +
# lifecycle.ignore_changes pattern as lambda-nfl-schedule-sync.tf.
#
# Reuses aws_iam_role.lambda_pipeline rather than a new role -- same
# reasoning as lambda-nfl-schedule-sync.tf's own comment.

resource "aws_cloudwatch_log_group" "nba_schedule_sync" {
  name              = "/aws/lambda/${var.project}-nba-schedule-sync"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "ingestion"
  })
}

data "archive_file" "nba_schedule_sync_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nba-schedule-sync-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nba_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nba_schedule_sync" {
  function_name = "${var.project}-nba-schedule-sync"
  description   = "Seeds the rest of the NBA season's scoreboards from ESPN (idempotent, skip-if-already-synced) so the frontend's upcoming list and season_projection.py's remaining_games input always have data ahead of daily ingest. Triggered by EventBridge Scheduler -- see scheduler-nba-schedule-sync.tf."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 270-day ceiling (handler.py's own SCHEDULE_SYNC_MAX_LOOKAHEAD_DAYS) x
  # the shared RateLimiter's 0.3s floor is ~81s worst case on the first
  # run after this shipped (before the idempotent skip has anything to
  # skip); real per-call latency on top of that floor easily pushes past
  # 120s. 300s gives real headroom for that one-time cost -- every run
  # after it is far cheaper (only new dates + still-unwritten preseason
  # dates get an ESPN call at all, see handler.py's own docstring).
  timeout     = 300
  memory_size = 256

  filename         = data.archive_file.nba_schedule_sync_placeholder.output_path
  source_code_hash = data.archive_file.nba_schedule_sync_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nba_schedule_sync.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "ingestion"
  })
}
