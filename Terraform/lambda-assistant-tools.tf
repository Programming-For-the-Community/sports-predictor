# The assistant's two tool Lambdas, invoked only by the agent runtimes'
# roles (iam-assistant-agents.tf). Code is deployed by
# assistant_lambdas_deploy.yml; Terraform creates them from the producers'
# placeholder ZIP (lambda-assistant-producers.tf).

resource "aws_cloudwatch_log_group" "assistant_public_tools" {
  name              = "/aws/lambda/${var.project}-assistant-public-tools"
  retention_in_days = 30

  tags = local.assistant_tags
}

# Reserved concurrency caps the assistant's share of the account's Lambda
# concurrency (400): each public-tools run can hold up to 4 predict-read calls
# (performance_overview.PARALLEL_READS), so 20 + 80 + 5 at most, beside the app's
# own peak (~165). Throttled calls are retried once (library/assistant/runtime.py).
locals {
  assistant_public_tools_concurrency = 20
  assistant_admin_tools_concurrency  = 5
}

resource "aws_lambda_function" "assistant_public_tools" {
  function_name                  = "${var.project}-assistant-public-tools"
  description                    = "Assistant tools for predictions and model performance: reads through predict-read and returns pruned, size-capped results to the agents."
  role                           = aws_iam_role.assistant_public_tools.arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "handler.lambda_handler"
  timeout                        = 30
  memory_size                    = 256
  reserved_concurrent_executions = local.assistant_public_tools_concurrency

  filename         = data.archive_file.assistant_producer_placeholder.output_path
  source_code_hash = data.archive_file.assistant_producer_placeholder.output_base64sha256

  environment {
    variables = merge(local.assistant_tool_settings, {
      PREDICT_READ_FUNCTION_NAME = aws_lambda_function.predict_read.function_name
      SPORT_REGISTRY_TABLE_NAME  = aws_dynamodb_table.sport_registry.name
      RAW_DATA_BUCKET_NAME       = local.raw_bucket_name
      AWS_ACCOUNT_ID             = var.account_id
    })
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.assistant_public_tools.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = local.assistant_tags
}

resource "aws_cloudwatch_log_group" "assistant_admin_tools" {
  name              = "/aws/lambda/${var.project}-assistant-admin-tools"
  retention_in_days = 30

  tags = local.assistant_tags
}

resource "aws_lambda_function" "assistant_admin_tools" {
  function_name                  = "${var.project}-assistant-admin-tools"
  description                    = "Admin assistant tools: cost, denied traffic, API traffic, Lambda and pipeline health, training runs, assistant usage. Reads S3, CloudWatch, DynamoDB, Step Functions."
  role                           = aws_iam_role.assistant_admin_tools.arn
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  handler                        = "handler.lambda_handler"
  timeout                        = 60
  memory_size                    = 512
  reserved_concurrent_executions = local.assistant_admin_tools_concurrency

  filename         = data.archive_file.assistant_producer_placeholder.output_path
  source_code_hash = data.archive_file.assistant_producer_placeholder.output_base64sha256

  environment {
    variables = merge(local.assistant_tool_settings, {
      AWS_ACCOUNT_ID              = var.account_id
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      API_GATEWAY_NAME            = aws_api_gateway_rest_api.main.name
      PROJECT_NAME                = var.project
      EVENTS_TABLE_NAME           = aws_dynamodb_table.events.name
      ASSISTANT_TABLE_NAME        = aws_dynamodb_table.assistant.name
      SPORT_REGISTRY_TABLE_NAME   = aws_dynamodb_table.sport_registry.name
      TRAINING_STATE_MACHINE_ARN  = aws_sfn_state_machine.training_orchestrator.arn
    })
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.assistant_admin_tools.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = local.assistant_tags
}
