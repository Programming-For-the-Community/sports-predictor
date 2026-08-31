# F1 ingest Lambda. Triggered daily by the shared sfn-ingest-orchestrator.tf,
# which invokes every active sport's own "${var.project}-<sport>-ingest"
# function by naming convention -- no per-sport scheduler file needed.
# Discovers which round(s) fall in a short trailing window around today's
# date (Jolpica has no "what's current" scoreboard-style endpoint, unlike
# ESPN -- see Source/library/http/f1.py's own docstring) and writes
# results/qualifying/sprint/pitstops raw JSON to S3; the normalize Lambda
# picks up from there via S3 event notification.
#
# Jolpica is keyless, so this Lambda has no third-party API key secret env
# var, same as NBA/NCAA MBB/PGA's ingest.
#
# Code is deployed by the f1_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`), not by Terraform -- the archive_file
# below is a placeholder the workflow overwrites, and
# lifecycle.ignore_changes keeps subsequent applies from reverting it.

resource "aws_cloudwatch_log_group" "f1_ingest" {
  name              = "/aws/lambda/${var.project}-f1-ingest"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

data "archive_file" "f1_ingest_placeholder" {
  type        = "zip"
  output_path = "${path.module}/f1-ingest-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via f1_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "f1_ingest" {
  function_name = "${var.project}-f1-ingest"
  description   = "Discovers which F1 race round(s) fall in today's trailing window and writes raw results/qualifying/sprint/pitstops JSON from Jolpica-F1 to S3. Triggered by the shared ingest orchestrator Step Function."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.f1_ingest_placeholder.output_path
  source_code_hash = data.archive_file.f1_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME      = aws_s3_bucket.raw_data_lake.bucket
      JOLPICA_API_ROOT_URL = var.jolpica_api_root_url
      JOLPICA_USER_AGENT   = var.jolpica_user_agent
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.f1_ingest.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "f1_ingest" {
  function_name = aws_lambda_function.f1_ingest.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}
