# Season-gate Lambda. Invoked synchronously by both orchestrator
# state machines (sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf)
# as a Task inside their ForEachSport Map. Shared across every sport, not
# sport-specific.
#
# Code is deployed by shared_lambdas_deploy.yml (via `aws lambda
# update-function-code`), not by Terraform, using a placeholder ZIP with
# lifecycle.ignore_changes.

resource "aws_cloudwatch_log_group" "season_gate" {
  name              = "/aws/lambda/${var.project}-season-gate"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

data "archive_file" "season_gate_placeholder" {
  type        = "zip"
  output_path = "${path.module}/season-gate-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via shared_lambdas_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "season_gate" {
  function_name = "${var.project}-season-gate"
  description   = "Reports whether today falls within a sport's season window (season_start/season_end). Invoked by both orchestrator state machines -- see sfn-ingest-orchestrator.tf/sfn-training-orchestrator.tf."
  role          = aws_iam_role.lambda_season_gate.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 10
  memory_size   = 128

  filename         = data.archive_file.season_gate_placeholder.output_path
  source_code_hash = data.archive_file.season_gate_placeholder.output_base64sha256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.season_gate.name
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}
