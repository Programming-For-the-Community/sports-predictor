# Assumed by both Step Functions state machines that replaced the old
# per-sport EventBridge Scheduler -> Lambda/ECS direct-invoke pattern (see
# sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf). One role for
# both state machines -- they read the same registry table and invoke the
# same shape of Lambda/ECS resources, so there's no least-privilege reason
# to split them.
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
    resources = ["arn:aws:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventForECSTaskRule"]
  }
}

resource "aws_iam_role_policy" "stepfunctions_orchestrator_permissions" {
  name   = "${var.project}-stepfunctions-orchestrator-permissions"
  role   = aws_iam_role.stepfunctions_orchestrator.id
  policy = data.aws_iam_policy_document.stepfunctions_orchestrator_permissions.json
}
