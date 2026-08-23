# Assumed by EventBridge Scheduler to start the two orchestrator state
# machines (sfn-ingest-orchestrator.tf, sfn-training-orchestrator.tf) on
# their own cron, plus the direct-invoke Lambda jobs below. (Normalize is
# triggered by an S3 event notification on the raw bucket, not by
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

  # Direct invoke, not through a state machine -- none of these are
  # per-sport/per-target fan-outs that would justify one:
  # scheduler-nfl-season-projection.tf invokes nfl_predict (one
  # computation), scheduler-nfl-schedule-sync.tf invokes
  # nfl_schedule_sync (walks all 23 weeks of a season internally),
  # scheduler-nfl-live-scores.tf invokes nfl_live_scores every 60s.
  # ncaafb_predict: scheduler-ncaafb-season-projection.tf's own target.
  # ncaafb_live_scores: scheduler-ncaafb-live-scores.tf's own target,
  # same every-60s reasoning as nfl_live_scores.
  # nba_predict: scheduler-nba-season-projection.tf's own target.
  # nba_live_scores: scheduler-nba-live-scores.tf's own target, same
  # every-60s reasoning. nba_schedule_sync: scheduler-nba-schedule-sync.tf's
  # own target.
  #
  # ncaambb_schedule_sync: scheduler-ncaambb-schedule-sync.tf's own
  # target. ncaambb_live_scores: scheduler-ncaambb-live-scores.tf's own
  # target, same every-60s reasoning as nba_live_scores/ncaafb_live_scores.
  #
  # The 3 predict-read functions: scheduler-predict-read-warmup.tf's own
  # 5-minute warmup ping.
  statement {
    sid     = "InvokeDirectLambdaJobs"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.nfl_predict.arn,
      aws_lambda_function.nfl_schedule_sync.arn,
      aws_lambda_function.nfl_live_scores.arn,
      aws_lambda_function.nfl_predict_read.arn,
      aws_lambda_function.ncaafb_predict.arn,
      aws_lambda_function.ncaafb_live_scores.arn,
      aws_lambda_function.ncaafb_predict_read.arn,
      aws_lambda_function.nba_predict.arn,
      aws_lambda_function.nba_live_scores.arn,
      aws_lambda_function.nba_schedule_sync.arn,
      aws_lambda_function.nba_predict_read.arn,
      aws_lambda_function.ncaambb_schedule_sync.arn,
      aws_lambda_function.ncaambb_predict.arn,
      aws_lambda_function.ncaambb_predict_read.arn,
      aws_lambda_function.ncaambb_live_scores.arn,
    ]
  }
}

resource "aws_iam_role_policy" "eventbridge_invoke_permissions" {
  name   = "${var.project}-eventbridge-invoke-permissions"
  role   = aws_iam_role.eventbridge_invoke.id
  policy = data.aws_iam_policy_document.eventbridge_invoke_permissions.json
}
