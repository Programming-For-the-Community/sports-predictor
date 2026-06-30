# Assumed by EventBridge Scheduler to trigger the ingest Lambdas on their
# polling cadence and to run the Fargate training pipeline on its training
# schedule. (Normalize is triggered by an S3 event notification on the raw
# bucket, not by EventBridge, so it isn't covered by this role.)
#
# iam:PassRole is scoped to the one ECS role in iam-ecs-pipeline.tf, since
# ecs:RunTask requires handing that role to ECS -- never grant PassRole on
# "*".
#
# The Step Functions orchestrator role (Phase 4) isn't created yet, since
# the state machine itself doesn't exist until that phase -- see the
# orchestration step in docs/PROJECT_PLAN.md.
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
    sid       = "InvokeIngestLambdas"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${var.account_id}:function:${var.project}-*-ingest"]
  }

  statement {
    sid       = "RunTrainingTask"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.region}:${var.account_id}:task-definition/${var.project}-*"]
  }

  statement {
    sid       = "PassEcsPipelineRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_pipeline.arn]
  }
}

resource "aws_iam_role_policy" "eventbridge_invoke_permissions" {
  name   = "${var.project}-eventbridge-invoke-permissions"
  role   = aws_iam_role.eventbridge_invoke.id
  policy = data.aws_iam_policy_document.eventbridge_invoke_permissions.json
}
