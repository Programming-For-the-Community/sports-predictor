# PGA ingest Lambda. Triggered daily by the shared sfn-ingest-orchestrator.tf,
# which invokes every active sport's own "${var.project}-<sport>-ingest"
# function by naming convention -- no per-sport scheduler file needed.
# Asks ESPN's golf/pga scoreboard what tournament is current for the
# target date, then fetches that tournament's richer leaderboard and
# writes it to S3; the normalize Lambda picks up from there via S3 event
# notification. See Source/aws-lambdas/pga/ingest/handler.py's own
# docstring for why this is date-based like every other sport's ingest
# even though schedule-sync (below) discovers PGA's whole season in one
# call.
#
# ESPN is keyless, so this Lambda has no third-party API key secret env
# var, same as NBA/NCAA MBB's ingest.
#
# Code is deployed by the pga_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`), not by Terraform -- the archive_file
# below is a placeholder the workflow overwrites, and
# lifecycle.ignore_changes keeps subsequent applies from reverting it.

resource "aws_cloudwatch_log_group" "pga_ingest" {
  name              = "/aws/lambda/${var.project}-pga-ingest"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

data "archive_file" "pga_ingest_placeholder" {
  type        = "zip"
  output_path = "${path.module}/pga-ingest-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via pga_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "pga_ingest" {
  function_name = "${var.project}-pga-ingest"
  description   = "Fetches the current PGA tournament's leaderboard from ESPN and writes raw JSON to S3. Triggered by the shared ingest orchestrator Step Function."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.pga_ingest_placeholder.output_path
  source_code_hash = data.archive_file.pga_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME   = aws_s3_bucket.raw_data_lake.bucket
      ESPN_API_ROOT_URL = var.espn_api_root_url
      ESPN_USER_AGENT   = var.espn_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.pga_ingest.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "pga_ingest" {
  function_name = aws_lambda_function.pga_ingest.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}
