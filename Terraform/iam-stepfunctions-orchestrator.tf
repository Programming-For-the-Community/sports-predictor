# Shared by sfn-ingest-orchestrator.tf and sfn-training-orchestrator.tf.
data "aws_iam_policy_document" "stepfunctions_orchestrator_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "stepfunctions_orchestrator" {
  name               = "${var.project}-stepfunctions-orchestrator-role"
  assume_role_policy = data.aws_iam_policy_document.stepfunctions_orchestrator_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

data "aws_iam_policy_document" "stepfunctions_orchestrator_permissions" {
  statement {
    sid       = "ReadSportRegistry"
    actions   = ["dynamodb:Scan", "dynamodb:Query"]
    resources = [aws_dynamodb_table.sport_registry.arn]
  }

  statement {
    sid       = "InvokeIngestLambdas"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${var.account_id}:function:${var.project}-*-ingest"]
  }

  statement {
    sid       = "InvokeSeasonGateLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.season_gate.arn]
  }

  statement {
    sid       = "InvokeEc2TrainingReaperLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.ec2_training_reaper.arn]
  }

  # Covers sfn-backfill-orchestrator.tf's task definitions too -- same
  # ${var.project}-* family.
  statement {
    sid       = "RunTrainingTasks"
    actions   = ["ecs:RunTask", "ecs:StopTask", "ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:${var.region}:${var.account_id}:task-definition/${var.project}-*"]
  }

  statement {
    sid       = "PassEcsPipelineRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_pipeline.arn]
  }

  # Each sport's backfill task uses its own role (iam-<sport>-backfill.tf),
  # not the shared ecs_pipeline role above.
  statement {
    sid     = "PassBackfillRoles"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.nfl_backfill.arn,
      aws_iam_role.ncaafb_backfill.arn,
      aws_iam_role.nba_backfill.arn,
      aws_iam_role.ncaambb_backfill.arn,
      aws_iam_role.pga_backfill.arn,
      aws_iam_role.f1_backfill.arn,
    ]
  }

  # Required for ecs:RunTask.sync -- Step Functions manages an EventBridge
  # rule to get ECS Task State Change events back.
  statement {
    sid       = "ManageEcsSyncEventRule"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["arn:aws:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"]
  }

  # Distributed Map child-execution permissions. Built from var.project
  # directly, not a resource reference, to avoid an apply-order cycle.
  statement {
    sid     = "RunTrainingDistributedMapChildren"
    actions = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution"]
    resources = [
      "arn:aws:states:${var.region}:${var.account_id}:stateMachine:${var.project}-training-orchestrator",
      "arn:aws:states:${var.region}:${var.account_id}:execution:${var.project}-training-orchestrator:*",
    ]
  }

  statement {
    sid     = "ScaleDownEc2TrainingCapacity"
    actions = ["autoscaling:SetDesiredCapacity"]
    resources = [
      "arn:aws:autoscaling:${var.region}:${var.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.project}-ec2-training-spot",
      "arn:aws:autoscaling:${var.region}:${var.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.project}-ec2-training-ondemand",
    ]
  }

  # Required for training_orchestrator's logging_configuration. "*" --
  # the log delivery API has no resource-level scoping.
  statement {
    sid = "DeliverExecutionLogsToCloudWatch"
    actions = [
      "logs:CreateLogDelivery",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }

  # Required for training_orchestrator's tracing_configuration. "*" --
  # X-Ray's write actions have no resource-level scoping.
  statement {
    sid       = "WriteXRayTraces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "stepfunctions_orchestrator_permissions" {
  name   = "${var.project}-stepfunctions-orchestrator-permissions"
  role   = aws_iam_role.stepfunctions_orchestrator.id
  policy = data.aws_iam_policy_document.stepfunctions_orchestrator_permissions.json
}

# Lets CloudWatch's log-delivery service write to vended-logs log groups.
resource "aws_cloudwatch_log_resource_policy" "vended_logs" {
  policy_name = "${var.project}-vended-logs"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource  = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/vendedlogs/*:*"
      }
    ]
  })
}

# Buffer for IAM/CloudWatch policy propagation.
resource "time_sleep" "iam_propagation" {
  depends_on = [
    aws_iam_role_policy.stepfunctions_orchestrator_permissions,
    aws_cloudwatch_log_resource_policy.vended_logs,
  ]
  triggers = {
    orchestrator_policy = data.aws_iam_policy_document.stepfunctions_orchestrator_permissions.json
    vended_logs_policy  = aws_cloudwatch_log_resource_policy.vended_logs.policy_document
  }
  create_duration = "15s"
}
