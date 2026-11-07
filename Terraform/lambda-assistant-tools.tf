# The assistant's two tool Lambdas, invoked only by the agent runtimes'
# roles (iam-assistant-agents.tf). Code is deployed by
# assistant_lambdas_deploy.yml; Terraform creates them from the producers'
# placeholder ZIP (lambda-assistant-producers.tf).

resource "aws_cloudwatch_log_group" "assistant_public_tools" {
  name              = "/aws/lambda/${var.project}-assistant-public-tools"
  retention_in_days = 30

  tags = local.assistant_tags
}

resource "aws_lambda_function" "assistant_public_tools" {
  function_name = "${var.project}-assistant-public-tools"
  description   = "Assistant tools for predictions and model performance: reads through predict-read and returns pruned, size-capped results to the agents."
  role          = aws_iam_role.assistant_public_tools.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "handler.lambda_handler"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.assistant_producer_placeholder.output_path
  source_code_hash = data.archive_file.assistant_producer_placeholder.output_base64sha256

  environment {
    variables = merge(local.assistant_tool_settings, {
      PREDICT_READ_FUNCTION_NAME = aws_lambda_function.predict_read.function_name
      SPORT_REGISTRY_TABLE_NAME  = aws_dynamodb_table.sport_registry.name
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
  function_name = "${var.project}-assistant-admin-tools"
  description   = "Admin assistant tools for cost, denied traffic, API traffic and Lambda health, from the producers' S3 aggregates and CloudWatch metrics."
  role          = aws_iam_role.assistant_admin_tools.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "handler.lambda_handler"
  timeout       = 60
  memory_size   = 512

  filename         = data.archive_file.assistant_producer_placeholder.output_path
  source_code_hash = data.archive_file.assistant_producer_placeholder.output_base64sha256

  environment {
    variables = merge(local.assistant_tool_settings, {
      AWS_ACCOUNT_ID              = var.account_id
      MODEL_ARTIFACTS_BUCKET_NAME = aws_s3_bucket.model_artifacts.bucket
      API_GATEWAY_NAME            = aws_api_gateway_rest_api.main.name
      PROJECT_NAME                = var.project
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
