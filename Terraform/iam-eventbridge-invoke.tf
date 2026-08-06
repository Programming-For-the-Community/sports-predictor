# Assumed by EventBridge Scheduler to start the two orchestrator state
# machines (sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf) on
# their own cron. (Normalize is triggered by an S3 event notification on
# the raw bucket, not by EventBridge, so it isn't covered by this role.)
#
# Used to also carry lambda:InvokeFunction/ecs:RunTask/iam:PassRole
# directly, back when EventBridge Scheduler invoked each sport's ingest
# Lambda and training ECS tasks itself. Now that a Step Functions state
# machine sits in between (see iam-stepfunctions-orchestrator.tf, which
# carries those permissions instead), this role only ever needs to start
# an execution.
data "aws_iam_policy_document" "eventbridge_invoke_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eventbridge_invoke" {
  name               = "${var.project}-eventbridge-invoke-role"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_invoke_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

data "aws_iam_policy_document" "eventbridge_invoke_permissions" {
  statement {
    sid     = "StartOrchestratorExecutions"
    actions = ["states:StartExecution"]
    resources = [
      aws_sfn_state_machine.ingest_orchestrator.arn,
      aws_sfn_state_machine.training_orchestrator.arn,
    ]
  }

  # Deliberate exception to this role's "goes through a state machine"
  # rule above (see this file's own docstring) -- scheduler-nfl-season-
  # projection.tf invokes nfl_predict directly since that job is one
  # computation with no per-sport/per-target fan-out to justify a state
  # machine in between.
  statement {
    sid       = "InvokeSeasonProjectionLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.nfl_predict.arn]
  }
}

resource "aws_iam_role_policy" "eventbridge_invoke_permissions" {
  name   = "${var.project}-eventbridge-invoke-permissions"
  role   = aws_iam_role.eventbridge_invoke.id
  policy = data.aws_iam_policy_document.eventbridge_invoke_permissions.json
}
