# NFL ingest Lambda. Triggered by EventBridge Scheduler -- see
# Terraform/scheduler-nfl-ingest.tf for the schedule and its Lambda
# permission. Fetches the current week's scoreboard and completed box
# scores from ESPN and writes raw JSON to S3; the normalize Lambda picks up
# from there via S3 event notification.
#
# Code is deployed by the nfl_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`) -- NOT by Terraform. The placeholder
# ZIP below satisfies Terraform's requirement that a Lambda function have
# code at creation time. lifecycle.ignore_changes ensures a subsequent
# `terraform apply` never reverts CI-deployed code back to the placeholder.
#
# No CURRENT_SEASON / CURRENT_SEASON_TYPE env vars -- the handler asks ESPN
# for "today's" scoreboard when it isn't given an explicit season/type/week,
# so the active season/season-type never needs a manual update here.

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
  function_name = "${var.project}-nfl-ingest"
  description   = "Fetches the current NFL scoreboard and completed box scores from ESPN and writes raw JSON to S3. Triggered by EventBridge Scheduler -- see scheduler-nfl-ingest.tf."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.nfl_ingest_placeholder.output_path
  source_code_hash = data.archive_file.nfl_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
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