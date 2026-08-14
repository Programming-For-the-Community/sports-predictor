# NBA schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
# see scheduler-nba-schedule-sync.tf. Walks the next 14 calendar days via
# ESPN's scoreboard-by-date endpoint and writes each date's results to S3
# -- normalize's existing S3 trigger (s3-raw-data-lake-notifications.tf)
# picks these up the same way daily ingest's output does. Exists for the
# same reason NFL's own schedule-sync does (daily ingest alone never seeds
# future dates ahead of time), but a fixed lookahead window rather than a
# full-season walk -- see the aws_lambda_function resource's own comment
# below for why, and for the known gap that leaves for season simulation.
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
  description   = "Seeds the next 14 days of NBA scoreboards from ESPN so the frontend's upcoming list always has data ahead of daily ingest. Triggered by EventBridge Scheduler -- see scheduler-nba-schedule-sync.tf."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # Only a 14-day lookahead (handler.py's own SCHEDULE_SYNC_LOOKAHEAD_DAYS),
  # NOT a full-season walk like NFL's/NCAAFB's own schedule-sync (600s
  # there reflects a real ~170-330-day-equivalent season walk) -- a
  # per-day walk across NBA's whole ~170-day regular season would be
  # 170+ ESPN calls every single scheduled run, in tension with this
  # phase's own API-call-minimization principle. 14 calls needs nowhere
  # near that ceiling. This is a real, known scope gap, not an oversight:
  # season simulation (Sub-phase 3A step 8) will very likely need the
  # full season's remaining games seeded the way NFL's own schedule-sync
  # provides for _season_standings_inputs' remaining_games, and 14 days
  # won't cover that -- revisit this Lambda (probably a full-season walk
  # with a skip-if-already-synced idempotency check per date, so it
  # doesn't re-pay 170+ calls every run) when step 8 is built, not before.
  timeout     = 120
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
