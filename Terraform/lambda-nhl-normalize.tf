# NHL normalize Lambda. Triggered by S3 PutObject events on the raw data
# lake, filtered to the nhl/ prefix so only NHL raw files invoke it. Reads
# the raw ESPN JSON written by nhl-ingest from S3, maps it to the project
# schema (overtime/shootout flags, probable goalies, skater and goalie box
# scores), and upserts entities/events/player_game_stats/team_game_stats.
# No ESPN calls of its own, so it needs no extra IAM beyond the shared
# lambda_pipeline role.
#
# Code is deployed by the nhl_deploy GitHub Actions workflow, not
# by Terraform.
#
# VPC-attached, private subnets, aws_security_group.lambda_pipeline
# (security-groups.tf) -- only touches S3/DynamoDB (via the Gateway
# Endpoints in vpc-endpoints.tf), never a sport's public data API, so
# unlike ingest/live-scores/schedule-sync it has no need for a public
# route out.

resource "aws_cloudwatch_log_group" "nhl_normalize" {
  name              = "/aws/lambda/${var.project}-nhl-normalize"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nhl"
    Component = "ingestion"
  })
}

data "archive_file" "nhl_normalize_placeholder" {
  type        = "zip"
  output_path = "${path.module}/nhl-normalize-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via nhl_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "nhl_normalize" {
  function_name = "${var.project}-nhl-normalize"
  description   = "Reads raw ESPN JSON written by nhl-ingest and upserts it into the entities, events, player_game_stats, and team_game_stats DynamoDB tables. Triggered by S3 ObjectCreated notifications on the nhl/ prefix."
  role          = aws_iam_role.lambda_pipeline.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # 300s/1024MB -- one S3 object per game (~38 players) or per team
  # roster, small enough for a plain serial loop.
  timeout     = 300
  memory_size = 1024

  filename         = data.archive_file.nhl_normalize_placeholder.output_path
  source_code_hash = data.archive_file.nhl_normalize_placeholder.output_base64sha256

  environment {
    variables = {
      AWS_ACCOUNT_ID               = var.account_id
      RAW_BUCKET_NAME              = aws_s3_bucket.raw_data_lake.bucket
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      # PipelineStorage's constructor requires all four table names
      # regardless of which one a given invocation actually writes to.
      TEAM_GAME_STATS_TABLE_NAME = aws_dynamodb_table.team_game_stats.name
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id, aws_subnet.private_c.id]
    security_group_ids = [aws_security_group.lambda_pipeline.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nhl_normalize.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "nhl"
    Component = "ingestion"
  })
}

resource "aws_lambda_function_event_invoke_config" "nhl_normalize" {
  function_name = aws_lambda_function.nhl_normalize.function_name

  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 21600
}

resource "aws_lambda_permission" "s3_invoke_nhl_normalize" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nhl_normalize.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data_lake.arn
}

# The bucket's S3 event notification config itself lives in
# s3-raw-data-lake-notifications.tf; a bucket can only have one
# aws_s3_bucket_notification resource.
