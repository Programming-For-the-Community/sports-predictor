# NCAAFB ingest Lambda. Triggered daily by the shared
# sfn-ingest-orchestrator.tf, which invokes every active sport's own
# "${var.project}-<sport>-ingest" function by naming convention -- no
# per-sport scheduler-ncaafb-ingest.tf is needed (unlike schedule-sync/
# live-scores below, which ARE invoked directly by their own scheduler).
# Fetches CFBD's per-week games/box-score data and writes raw JSON to S3,
# plus coach/ranking enrichment (see
# Source/aws-lambdas/ncaafb/ingest/enrichment.py) -- the normalize Lambda
# picks up from S3 the same way lambda-nfl-normalize.tf's does.
#
# Code is deployed by the ncaafb_data_pipeline GitHub Actions workflow (via
# `aws lambda update-function-code`) -- NOT by Terraform. Same placeholder-
# ZIP + lifecycle.ignore_changes pattern as lambda-nfl-ingest.tf.
#
# CFBD requires an API key, unlike NFL's keyless ESPN source --
# CFBD_API_KEY_SECRET_ARN + CFBD_API_KEY_SECRET_FIELD resolve the ingest key
# specifically (not the backfill key) from the shared secret at cold start.
# Reuses aws_iam_role.lambda_pipeline (iam-lambda-pipeline.tf), which
# already grants secretsmanager:GetSecretValue on that secret.

resource "aws_cloudwatch_log_group" "ncaafb_ingest" {
  name              = "/aws/lambda/${var.project}-ncaafb-ingest"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

data "archive_file" "ncaafb_ingest_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ncaafb-ingest-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via ncaafb_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ncaafb_ingest" {
  function_name = "${var.project}-ncaafb-ingest"
  description   = "Fetches the current week's CFBD games/box-scores/betting-lines plus coach/ranking enrichment, and writes raw JSON to S3."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.ncaafb_ingest_placeholder.output_path
  source_code_hash = data.archive_file.ncaafb_ingest_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME           = aws_s3_bucket.raw_data_lake.bucket
      CFBD_API_ROOT_URL         = var.cfbd_api_root_url
      CFBD_API_KEY_SECRET_ARN   = var.third_party_api_key_secret_arn
      CFBD_API_KEY_SECRET_FIELD = "ncaa_fb_ingest_key"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ncaafb_ingest.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "ncaafb_ingest" {
  function_name = aws_lambda_function.ncaafb_ingest.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}
