# EC2 training-reaper Lambda. Independent backstop against orphaned EC2
# training instances -- see the handler's own docstring
# (Source/aws-lambdas/shared/ec2-training-reaper/handler.py) for why this
# exists alongside sfn-training-orchestrator.tf's own explicit scale-down
# states. Shared/utility Lambda, not sport-specific -- same
# placeholder-ZIP + shared_lambdas_deploy.yml deployment pattern as
# lambda-season-gate.tf.

resource "aws_cloudwatch_log_group" "ec2_training_reaper" {
  name              = "/aws/lambda/${var.project}-ec2-training-reaper"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}

data "archive_file" "ec2_training_reaper_placeholder" {
  type        = "zip"
  output_path = "${path.module}/ec2-training-reaper-placeholder.zip"
  source {
    content  = "def lambda_handler(event, context): return {'statusCode': 200, 'body': 'placeholder -- deploy via shared_lambdas_deploy workflow'}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "ec2_training_reaper" {
  function_name = "${var.project}-ec2-training-reaper"
  description   = "Terminates any EC2 training instance idle (zero running/pending ECS tasks) past a short grace period -- independent backstop for orphaned instances a manually-stopped training run or slow native ECS scale-in would otherwise leave running."
  role          = aws_iam_role.lambda_ec2_training_reaper.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.ec2_training_reaper_placeholder.output_path
  source_code_hash = data.archive_file.ec2_training_reaper_placeholder.output_base64sha256

  environment {
    variables = {
      ECS_CLUSTER_NAME     = aws_ecs_cluster.main.name
      GRACE_PERIOD_MINUTES = "5"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ec2_training_reaper.name
  }

  # Same reasoning as lambda-season-gate.tf's tracing_config -- puts this
  # Lambda on the CloudWatch Application Map too. At a rate(10 minutes)
  # schedule (scheduler-ec2-training-reaper.tf) that's ~4,300 traces/month,
  # still well inside X-Ray's 100k-traces-recorded free tier.
  tracing_config {
    mode = "Active"
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "training"
  })
}
