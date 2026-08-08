# Assumed by EventBridge Scheduler to start the two orchestrator state
# machines (sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf) on
# their own cron, plus the two direct-invoke Lambda jobs below. (Normalize
# is triggered by an S3 event notification on the raw bucket, not by
# EventBridge, so it isn't covered by this role.)
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

  # Direct invoke, not through a state machine -- none of the three are
  # per-sport/per-target fan-outs that would justify one:
  # scheduler-nfl-season-projection.tf invokes nfl_predict (one
  # computation), scheduler-nfl-schedule-sync.tf invokes
  # nfl_schedule_sync (walks all 23 weeks of a season internally),
  # scheduler-nfl-live-scores.tf invokes nfl_live_scores every 60s.
  statement {
    sid     = "InvokeDirectLambdaJobs"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.nfl_predict.arn,
      aws_lambda_function.nfl_schedule_sync.arn,
      aws_lambda_function.nfl_live_scores.arn,
    ]
  }
}

resource "aws_iam_role_policy" "eventbridge_invoke_permissions" {
  name   = "${var.project}-eventbridge-invoke-permissions"
  role   = aws_iam_role.eventbridge_invoke.id
  policy = data.aws_iam_policy_document.eventbridge_invoke_permissions.json
}
