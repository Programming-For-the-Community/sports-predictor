# NFL ingest Lambda. Triggered by EventBridge Scheduler on a weekly cadence
# (Tue + Wed mornings UTC to catch Monday Night Football and any delayed
# stat publications). Fetches the current week's scoreboard and completed
# box scores from ESPN and writes raw JSON to S3; the normalize Lambda picks
# up from there via S3 event notification.
#
# Code is deployed by the nfl_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`) -- NOT by Terraform. The placeholder
# ZIP below satisfies Terraform's requirement that a Lambda function have
# code at creation time. lifecycle.ignore_changes ensures a subsequent
# `terraform apply` never reverts CI-deployed code back to the placeholder.
#
# CURRENT_SEASON / CURRENT_SEASON_TYPE are env vars so the active season
# can be updated without a code change -- just update these values and
# run `terraform apply`.

resource "aws_cloudwatch_log_group" "nfl_ingest" {
  name              = "/aws/lambda/${var.project}-nfl-ingest"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

data "archive_file" "nfl_ingest_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nfl-ingest-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nfl_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nfl_ingest" {
  function_name    = "${var.project}-nfl-ingest"
  role             = aws_iam_role.lambda_pipeline.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  timeout          = 300
  memory_size      = 256

  filename         = data.archive_file.nfl_ingest_placeholder.output_path
  source_code_hash = data.archive_file.nfl_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME      = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL    = var.espn_api_root_url
      CURRENT_SEASON       = "2025"
      CURRENT_SEASON_TYPE  = "2"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nfl_ingest.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "nfl_ingest" {
  function_name = aws_lambda_function.nfl_ingest.function_name

  # Lambda retries async invocations up to 2 times by default; this resource
  # makes that explicit and adds a 1-hour event age ceiling so stale
  # EventBridge payloads are discarded rather than queued indefinitely.
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "eventbridge_invoke_nfl_ingest" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nfl_ingest.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.nfl_ingest.arn
}

resource "aws_scheduler_schedule" "nfl_ingest" {
  name       = "${var.project}-nfl-ingest"
  group_name = "default"

  # Tuesdays and Wednesdays at 10:00 UTC -- catches Monday Night Football
  # (completed by ~04:00 UTC) with margin, plus a Wednesday retry for any
  # delayed box score publications.
  schedule_expression          = "cron(0 10 ? * TUE,WED *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.nfl_ingest.arn
    role_arn = aws_iam_role.eventbridge_invoke.arn
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "orchestration"
  })
}