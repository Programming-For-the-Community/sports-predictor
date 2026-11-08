# The role AgentCore Evaluations runs as (agentcore-evaluations.tf): reads
# the assistant runtimes' spans with Logs Insights, writes scores to the
# results log group, and calls the custom judge model. Built-in evaluators
# run on AWS's own models and need no model permission here.

data "aws_iam_policy_document" "assistant_evaluations_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values = [
        "arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:evaluator/*",
        "arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:online-evaluation-config/*",
      ]
    }
  }
}

resource "aws_iam_role" "assistant_evaluations" {
  name               = "${var.project}-assistant-evaluations"
  assume_role_policy = data.aws_iam_policy_document.assistant_evaluations_assume.json

  tags = local.assistant_tags
}

locals {
  # Spans arrive in each runtime's own log group (unified telemetry), or in
  # the shared aws/spans group for any recorded before that.
  assistant_span_log_group_arns = concat(
    [for name in values(local.assistant_runtime_log_groups) : "arn:aws:logs:${var.region}:${var.account_id}:log-group:${name}"],
    ["arn:aws:logs:${var.region}:${var.account_id}:log-group:aws/spans"],
  )
}

data "aws_iam_policy_document" "assistant_evaluations" {
  statement {
    sid       = "QuerySpans"
    actions   = ["logs:StartQuery"]
    resources = [for arn in local.assistant_span_log_group_arns : "${arn}:*"]
  }

  # GetQueryResults and DescribeLogGroups can't be scoped to a log group.
  statement {
    sid       = "ReadQueryResults"
    actions   = ["logs:GetQueryResults", "logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "IndexSpans"
    actions   = ["logs:DescribeIndexPolicies", "logs:PutIndexPolicy"]
    resources = concat(local.assistant_span_log_group_arns, [for arn in local.assistant_span_log_group_arns : "${arn}:*"])
  }

  statement {
    sid       = "WriteResults"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.assistant_evaluations.arn}:*"]
  }

  statement {
    sid     = "InvokeJudge"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = concat(
      startswith(local.assistant_judge_model_id, "us.") ? ["arn:aws:bedrock:${var.region}:${var.account_id}:inference-profile/${local.assistant_judge_model_id}"] : [],
      [for r in local.assistant_model_regions : "arn:aws:bedrock:${r}::foundation-model/${local.assistant_judge_foundation_model}"],
    )
  }
}

resource "aws_iam_role_policy" "assistant_evaluations" {
  name   = "${var.project}-assistant-evaluations"
  role   = aws_iam_role.assistant_evaluations.id
  policy = data.aws_iam_policy_document.assistant_evaluations.json
}

# The service checks the role when a configuration is created, so the
# configurations wait for its policy to propagate.
resource "time_sleep" "assistant_evaluations_iam_propagation" {
  depends_on      = [aws_iam_role_policy.assistant_evaluations]
  triggers        = { policy = data.aws_iam_policy_document.assistant_evaluations.json }
  create_duration = "30s"
}
