# Prediction-scheduler Lambda. Invoked every 5 minutes by EventBridge
# Scheduler (scheduler-prediction-scheduler.tf). Shared across the
# head-to-head sports: shortly before each event's kickoff it re-runs the
# sport's ingest (fresh injuries/rosters/depth charts), then triggers the
# sport's predict Lambda to write the event's immutable pre-kickoff
# snapshot -- see library/serving/prediction_scheduler.py.
#
# Code is deployed by shared_lambdas_deploy.yml (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.

resource "aws_cloudwatch_log_group" "prediction_scheduler" {
  name              = "/aws/lambda/${var.project}-prediction-scheduler"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "prediction"
  })
}

data "archive_file" "prediction_scheduler_placeholder" {
  type        = "zip"
  output_path = "${path.module}/prediction-scheduler-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via shared_lambdas_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "prediction_scheduler" {
  function_name = "${var.project}-prediction-scheduler"
  description   = "Every 5 minutes: re-runs a sport's ingest ~30 min before kickoff (fresh injuries/rosters), then triggers its predict Lambda ~15 min before to write the pre-kickoff snapshot. PGA/F1: one snapshot at event start."
  role          = aws_iam_role.lambda_prediction_scheduler.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.prediction_scheduler_placeholder.output_path
  source_code_hash = data.archive_file.prediction_scheduler_placeholder.output_base64sha256

  environment {
    variables = {
      AWS_ACCOUNT_ID         = var.account_id
      PROJECT_NAME           = var.project
      EVENTS_TABLE_NAME      = aws_dynamodb_table.events.name
      PREDICTIONS_TABLE_NAME = aws_dynamodb_table.predictions.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.prediction_scheduler.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "prediction"
  })
}
