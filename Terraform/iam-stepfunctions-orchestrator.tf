# Assumed by both Step Functions orchestrators (sfn-ingest-orchestrator.tf,
# sfn-training-orchestrator.tf). One role for both -- they read the same
# registry table and invoke the same shape of Lambda/ECS resources, so
# there's no least-privilege reason to split them.
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

  # season-gate (lambda-season-gate.tf) -- doesn't match the *-ingest
  # pattern above since it's shared, not per-sport, so it needs its own
  # statement.
  statement {
    sid       = "InvokeSeasonGateLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.season_gate.arn]
  }

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

  # The ecs:RunTask.sync integration (used so a Map iteration waits for
  # the ECS task to finish) works by having Step Functions manage an
  # EventBridge rule that forwards ECS Task State Change events back to
  # it -- this needs its own permissions on top of ecs:RunTask itself, or
  # .sync executions hang until they time out.
  statement {
    sid       = "ManageEcsSyncEventRule"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["arn:aws:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"]
  }

  # Distributed Map child-execution permissions (see sfn-training-
  # orchestrator.tf). Resources are built from var.project directly, not
  # a reference to aws_sfn_state_machine.training_orchestrator.arn/.name --
  # a resource reference would make this policy depend on that state
  # machine and invert the apply order it actually needs.
  statement {
    sid     = "RunTrainingDistributedMapChildren"
    actions = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution"]
    resources = [
      "arn:aws:states:${var.region}:${var.account_id}:stateMachine:${var.project}-training-orchestrator",
      "arn:aws:states:${var.region}:${var.account_id}:execution:${var.project}-training-orchestrator:*",
    ]
  }

  # sfn-training-orchestrator.tf's own ScaleDownTrainingSpotCapacity/
  # ScaleDownTrainingOnDemandCapacity states -- explicit cleanup once the
  # run finishes, rather than waiting on ECS managed_scaling's own slower,
  # reactive scale-in (see that file's own comment on those two states).
  # Scoped to the two specific EC2 training ASGs, not autoscaling:* broadly.
  statement {
    sid     = "ScaleDownEc2TrainingCapacity"
    actions = ["autoscaling:SetDesiredCapacity"]
    resources = [
      "arn:aws:autoscaling:${var.region}:${var.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.project}-ec2-training-spot",
      "arn:aws:autoscaling:${var.region}:${var.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.project}-ec2-training-ondemand",
    ]
  }

  # training_orchestrator's logging_configuration needs these to deliver
  # execution logs to CloudWatch. Resource "*" is required since the log
  # delivery API these actions cover operates on the account's log
  # delivery configurations generally, not any one resource.
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
}

resource "aws_iam_role_policy" "stepfunctions_orchestrator_permissions" {
  name   = "${var.project}-stepfunctions-orchestrator-permissions"
  role   = aws_iam_role.stepfunctions_orchestrator.id
  policy = data.aws_iam_policy_document.stepfunctions_orchestrator_permissions.json
}

# Account-level resource policy letting CloudWatch's log-delivery service
# (not the state machine's own role) write to vended-logs log groups --
# separate from the IAM role permissions above, and required for
# training_orchestrator's logging_configuration.
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

# Buffer for IAM/CloudWatch policy propagation -- training_orchestrator
# (sfn-training-orchestrator.tf) depends_on this. triggers ties it to the
# policies' actual content so it re-waits whenever either changes, not
# just on initial creation.
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
