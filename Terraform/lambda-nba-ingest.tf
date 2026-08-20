# NBA ingest Lambda. Triggered daily by the shared sfn-ingest-orchestrator.tf,
# which invokes every active sport's own "${var.project}-<sport>-ingest"
# function by naming convention -- no per-sport scheduler file needed.
# Fetches the day's scoreboard + completed box-scores from ESPN's
# basketball/nba endpoints and writes raw JSON to S3; the normalize Lambda
# picks up from there via S3 event notification.
#
# ESPN is keyless, so this Lambda has no CFBD_API_KEY_SECRET_ARN/FIELD env
# vars.
#
# Code is deployed by the nba_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`), not by Terraform -- the archive_file
# below is a placeholder the workflow overwrites, and
# lifecycle.ignore_changes keeps subsequent applies from reverting it.

resource "aws_cloudwatch_log_group" "nba_ingest" {
  name              = "/aws/lambda/${var.project}-nba-ingest"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "ingestion"
  })
}

data "archive_file" "nba_ingest_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nba-ingest-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nba_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nba_ingest" {
  function_name = "${var.project}-nba-ingest"
  description   = "Fetches the current NBA scoreboard and completed box scores from ESPN and writes raw JSON to S3. Triggered by the shared ingest orchestrator Step Function."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.nba_ingest_placeholder.output_path
  source_code_hash = data.archive_file.nba_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nba_ingest.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "nba_ingest" {
  function_name = aws_lambda_function.nba_ingest.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}
