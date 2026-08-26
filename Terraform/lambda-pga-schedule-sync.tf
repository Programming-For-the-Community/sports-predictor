# PGA schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
# see scheduler-pga-schedule-sync.tf. In ONE invocation, discovers the
# whole current season's tournament calendar from a single ESPN scoreboard
# call and writes each tournament's leaderboard to S3; normalize's
# existing S3 trigger (s3-raw-data-lake-notifications.tf) picks these up
# the same way daily ingest's output does.
#
# Genuinely cheaper than every other sport's own schedule-sync Lambda --
# it does NOT walk individual calendar dates (NBA/NFL/NCAAFB/NCAAMBB all
# do, since a day-by-day schedule is the only way to discover their
# games). See Source/aws-lambdas/pga/schedule-sync/handler.py's own
# docstring for why one scoreboard call is enough for PGA.
#
# Code is deployed by the pga_data_pipeline workflow (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.
#
# Reuses aws_iam_role.lambda_pipeline rather than a new role.

resource "aws_cloudwatch_log_group" "pga_schedule_sync" {
  name              = "/aws/lambda/${var.project}-pga-schedule-sync"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

data "archive_file" "pga_schedule_sync_placeholder" {
  type        = "zip"
  output_path = "${path.module}/pga-schedule-sync-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via pga_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "pga_schedule_sync" {
  function_name = "${var.project}-pga-schedule-sync"
  description   = "Seeds/refreshes the current PGA season's whole tournament calendar from ESPN (idempotent, skip-if-already-synced beyond a startDate-based refresh window). Triggered by EventBridge Scheduler."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # A season's ~45-51 tournaments, one leaderboard call each at the shared
  # RateLimiter's 0.3s floor, is well under a minute even on a full,
  # nothing-already-synced run -- 300s/256MB mirrors every other sport's
  # schedule-sync budget rather than being tuned down, since the margin
  # costs nothing.
  timeout     = 300
  memory_size = 256

  filename         = data.archive_file.pga_schedule_sync_placeholder.output_path
  source_code_hash = data.archive_file.pga_schedule_sync_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.pga_schedule_sync.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}
