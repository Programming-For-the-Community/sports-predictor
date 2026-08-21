# NCAA MBB ingest Lambda. Triggered daily by the shared sfn-ingest-orchestrator.tf,
# which invokes every active sport's own "${var.project}-<sport>-ingest"
# function by naming convention -- no per-sport scheduler file needed.
# Fetches the day's scoreboard + completed box-scores from ESPN's
# basketball/mens-college-basketball endpoints and writes raw JSON to S3;
# the normalize Lambda picks up from there via S3 event notification.
#
# ESPN is keyless, so this Lambda has no CFBD_API_KEY_SECRET_ARN/FIELD env
# vars.
#
# Code is deployed by the ncaambb_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`), not by Terraform -- the archive_file
# below is a placeholder the workflow overwrites, and
# lifecycle.ignore_changes keeps subsequent applies from reverting it.

resource "aws_cloudwatch_log_group" "ncaambb_ingest" {
  name              = "/aws/lambda/${var.project}-ncaambb-ingest"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

data "archive_file" "ncaambb_ingest_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaambb-ingest-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaambb_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaambb_ingest" {
  function_name = "${var.project}-ncaambb-ingest"
  description   = "Fetches the current NCAA MBB scoreboard and completed box scores from ESPN and writes raw JSON to S3. Triggered by the shared ingest orchestrator Step Function."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 600s/512MB -- higher than NBA's 300s/256MB on both counts. D1 has
  # ~362 teams (every one re-fetched every run) vs NBA's ~30, and a single
  # busy Saturday can carry ~150-155 games vs NBA's ~15 (confirmed live,
  # 2026-08-19 -- see project-ncaambb-onboarding memory). handler.py's own
  # VOLUME docstring section covers the ThreadPoolExecutor concurrency this
  # timeout/memory budget is sized for; even with that concurrency, the
  # shared RateLimiter's 0.3s floor alone puts a worst-case (fully
  # unseeded roster fetch + a max-volume game night) run's floor north of
  # NBA's own worst case.
  timeout     = 600
  memory_size = 512

  filename         = data.archive_file.ncaambb_ingest_placeholder.output_path
  source_code_hash = data.archive_file.ncaambb_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaambb_ingest.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "ncaambb_ingest" {
  function_name = aws_lambda_function.ncaambb_ingest.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}
