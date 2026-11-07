# The admin assistant's two data producers. Both write small JSON aggregates
# under assistant/admin/ in the model-artifacts bucket; the admin agent's
# tools read only those, never Cost Explorer or raw logs. Invoked by
# scheduler-assistant-producers.tf.
#
# Code is deployed by assistant_lambdas_deploy.yml (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.

locals {
  # Source/library/assistant/settings.py's ProducerSettings.
  assistant_producer_settings = {
    COST_RESTATED_DAYS     = "3"
    COST_BACKFILL_MONTHS   = "12"
    COST_MONTH_REPULL_DAY  = "5"
    TRAFFIC_LOOKBACK_HOURS = "2"
  }
}

data "archive_file" "assistant_producer_placeholder" {
  type        = "zip"
  output_path = "${path.module}/assistant-producer-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via assistant_lambdas_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_cloudwatch_log_group" "assistant_cost_snapshot" {
  name              = "/aws/lambda/${var.project}-assistant-cost-snapshot"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}

resource "aws_lambda_function" "assistant_cost_snapshot" {
  function_name = "${var.project}-assistant-cost-snapshot"
  description   = "Daily: pulls this project's Project-tagged spend from Cost Explorer into S3 aggregates the admin assistant reads. See scheduler-assistant-producers.tf."
  role          = aws_iam_role.lambda_assistant_cost_snapshot.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  filename         = data.archive_file.assistant_producer_placeholder.output_path
  source_code_hash = data.archive_file.assistant_producer_placeholder.output_base64sha256

  environment {
    variables = merge(local.assistant_producer_settings, {
      AWS_ACCOUNT_ID              = var.account_id
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      PROJECT_BUDGET_NAME         = aws_budgets_budget.project.name
      # Cost Explorer and Budgets are served from us-east-1 only.
      COST_EXPLORER_REGION = "us-east-1"
    })
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.assistant_cost_snapshot.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}

resource "aws_cloudwatch_log_group" "assistant_traffic_aggregate" {
  name              = "/aws/lambda/${var.project}-assistant-traffic-aggregate"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}

resource "aws_lambda_function" "assistant_traffic_aggregate" {
  function_name = "${var.project}-assistant-traffic-aggregate"
  description   = "Hourly: counts blocked and denied requests from both WAF log groups and the API Gateway access log into S3 aggregates the admin assistant reads."
  role          = aws_iam_role.lambda_assistant_traffic_aggregate.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "handler.lambda_handler"
  # A {"backfill_days": 30} run issues 90 Logs Insights queries in sequence.
  timeout     = 900
  memory_size = 256

  filename         = data.archive_file.assistant_producer_placeholder.output_path
  source_code_hash = data.archive_file.assistant_producer_placeholder.output_base64sha256

  environment {
    variables = merge(local.assistant_producer_settings, {
      AWS_ACCOUNT_ID               = var.account_id
      MODEL_ARTIFACTS_BUCKET_NAME  = aws_s3_bucket.model_artifacts.bucket
      WAF_CLOUDFRONT_LOG_GROUP     = aws_cloudwatch_log_group.waf_cloudfront.name
      WAF_CLOUDFRONT_LOG_REGION    = "us-east-1"
      WAF_API_GATEWAY_LOG_GROUP    = aws_cloudwatch_log_group.waf_api_gateway.name
      API_GATEWAY_ACCESS_LOG_GROUP = aws_cloudwatch_log_group.api_gateway_access.name
    })
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.assistant_traffic_aggregate.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}
