# NFL schedule-sync Lambda. Triggered directly by EventBridge Scheduler --
# see scheduler-nfl-schedule-sync.tf. Walks every week of the current NFL
# season in ONE invocation and writes each week's scoreboard to S3, the
# same raw bucket + key pattern daily ingest already uses -- normalize's
# existing S3 trigger picks these up automatically, no separate wiring
# needed. See Source/aws-lambdas/nfl/schedule-sync/handler.py's own
# docstring for why this is a dedicated Lambda rather than routed through
# ingest/handler.py.
#
# Code is deployed by the nfl_data_pipeline workflow (via `aws lambda
# update-function-code`) -- NOT by Terraform. Same placeholder-ZIP +
# lifecycle.ignore_changes pattern as lambda-nfl-ingest.tf.
#
# Reuses aws_iam_role.lambda_pipeline rather than a new role -- this
# Lambda needs exactly one permission (s3:PutObject on the raw bucket),
# already a strict subset of what that role grants ingest/normalize.

resource "aws_cloudwatch_log_group" "nfl_schedule_sync" {
  name              = "/aws/lambda/${var.project}-nfl-schedule-sync"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

data "archive_file" "nfl_schedule_sync_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nfl-schedule-sync-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nfl_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nfl_schedule_sync" {
  function_name = "${var.project}-nfl-schedule-sync"
  description   = "Walks every week of the current NFL season and writes each week's scoreboard to S3. Triggered by EventBridge Scheduler -- see scheduler-nfl-schedule-sync.tf."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # ~23 sequential ESPN calls, rate-limited (library/http/client.py's
  # default min_interval_seconds=0.3) plus each call's own up-to-5-attempt
  # retry/backoff on failure -- generous headroom over the realistic
  # common case (well under a minute) for the worst case where several
  # weeks each need their own retries.
  timeout     = 600
  memory_size = 256

  filename         = data.archive_file.nfl_schedule_sync_placeholder.output_path
  source_code_hash = data.archive_file.nfl_schedule_sync_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nfl_schedule_sync.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}
