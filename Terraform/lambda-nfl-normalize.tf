# NFL normalize Lambda. Triggered by S3 PutObject events on the raw data
# lake, filtered to the nfl/ prefix so only NFL raw files invoke it. Reads
# the raw ESPN JSON from S3, maps it to the project schema, and upserts the
# results into the entities, events, and player_game_stats DynamoDB tables.
#
# Code is deployed by the nfl_data_pipeline GitHub Actions workflow -- NOT
# by Terraform. See the placeholder / lifecycle note in lambda-nfl-ingest.tf
# for the same rationale.
#
# The lambda_permission must exist before the S3 notification is created,
# enforced via depends_on on the notification resource.

resource "aws_cloudwatch_log_group" "nfl_normalize" {
  name              = "/aws/lambda/${var.project}-nfl-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

data "archive_file" "nfl_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nfl-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nfl_data_pipeline workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nfl_normalize" {
  function_name = "${var.project}-nfl-normalize"
  description   = "Reads raw ESPN JSON written by nfl-ingest and upserts it into the entities, events, and player_game_stats DynamoDB tables. Triggered by S3 ObjectCreated notifications on the nfl/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.nfl_normalize_placeholder.output_path
  source_code_hash = data.archive_file.nfl_normalize_placeholder.output_base64sha256

  environment {
    variables = {
      RAW_BUCKET_NAME              = aws_s3_bucket.raw_data_lake.bucket
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nfl_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "nfl_normalize" {
  function_name = aws_lambda_function.nfl_normalize.function_name

  # S3 notifications can be delayed; allow up to 6 hours before discarding.
  # If all retries are exhausted, re-processing is trivial: re-PUT the S3
  # object and the notification fires again.
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_nfl_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nfl_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

resource "aws_s3_bucket_notification" "nfl_normalize_trigger" {
  bucket = aws_s3_bucket.raw_data_lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.nfl_normalize.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "nfl/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.s3_invoke_nfl_normalize]
}