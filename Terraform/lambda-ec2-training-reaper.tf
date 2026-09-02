# EC2 training-reaper Lambda. Independent backstop against orphaned EC2
# training instances -- see the handler's own docstring
# (Source/aws-lambdas/shared/ec2-training-reaper/handler.py) for why this
# exists alongside sfn-training-orchestrator.tf's own explicit scale-down
# states. Shared/utility Lambda, not sport-specific -- same
# placeholder-ZIP + shared_lambdas_deploy.yml deployment pattern as
# lambda-season-gate.tf.
#
# No recurring schedule triggers this Lambda at all -- it's invoked
# directly by sfn-training-orchestrator.tf's own InvokeReaperAfterCompletion
# state (a normal SUCCEEDED completion), by eventbridge-training-orchestrator-
# terminal.tf's own EventBridge rule (an ABORTED/FAILED/TIMED_OUT
# completion), and by its own self-created one-time retry schedules
# (handler.py) -- every invocation is caused by something that actually
# just happened, never a blind poll.

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
      PROJECT_TAG_VALUE    = var.project
      GRACE_PERIOD_MINUTES = "5"
      # Self-scheduling retry (handler.py) -- how long until the next
      # check, and how many times it may reschedule itself before giving
      # up. 3 x 15 minutes = 45 minutes, comfortably past the 30-40
      # minutes a real run's own lingering instances have taken to
      # actually terminate after DesiredCapacity was already set to 0.
      RETRY_DELAY_MINUTES  = "15"
      MAX_RETRIES          = "3"
      SCHEDULER_GROUP_NAME = aws_scheduler_schedule_group.sports_predictor.name
      SCHEDULER_ROLE_ARN   = aws_iam_role.eventbridge_invoke.arn
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ec2_training_reaper.name
  }

  # Same reasoning as lambda-season-gate.tf's tracing_config -- puts this
  # Lambda on the X-Ray Trace Map too. Invocation volume is bounded by
  # real completion/abort events now, not a fixed schedule -- nowhere
  # near X-Ray's 100k-traces-recorded free tier.
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
