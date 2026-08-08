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

  # The ecs:RunTask.sync integration (used so a Map iteration actually
  # waits for the ECS task to finish, rather than firing-and-forgetting)
  # works by having Step Functions create/manage an EventBridge rule that
  # forwards ECS Task State Change events back to it -- this needs its own
  # permissions on top of ecs:RunTask itself, or .sync executions hang
  # until they time out rather than ever seeing the task complete. Per
  # AWS's own documented requirement for this integration pattern, not
  # something ecs:RunTask alone implies.
  statement {
    sid       = "ManageEcsSyncEventRule"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["arn:aws:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"]
  }

  # sfn-training-orchestrator.tf's TrainAllTargets Map runs in Distributed
  # mode -- each iteration is its own child execution of this same state
  # machine, not an in-place step, so starting/polling/stopping those
  # iterations needs its own states: permissions on top of everything
  # above. Per AWS's own documented requirement for Distributed Map.
  statement {
    sid     = "RunTrainingDistributedMapChildren"
    actions = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution"]
    resources = [
      aws_sfn_state_machine.training_orchestrator.arn,
      "arn:aws:states:${var.region}:${var.account_id}:execution:${aws_sfn_state_machine.training_orchestrator.name}:*",
    ]
  }

  # training_orchestrator's logging_configuration (sfn-training-
  # orchestrator.tf) needs these to deliver execution logs to CloudWatch.
  # Resource "*" is per AWS's own documented policy for this feature, not
  # a broadening of scope by choice -- the log delivery API these actions
  # cover (CreateLogDelivery/ListLogDeliveries/etc.) operates on the
  # account's log delivery configurations generally, not any one
  # resource, so it can't be scoped to a specific log group ARN.
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
