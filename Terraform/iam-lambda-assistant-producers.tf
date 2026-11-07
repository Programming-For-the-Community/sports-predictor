# One role per assistant producer. Cost Explorer's read actions can't be
# scoped to a resource, so ce:* is on "*"; the Project-tag filter is enforced
# in library.assistant.cost_snapshot instead (and a unit test fails the build
# if any request goes out without it). Neither role can reach the model or
# the agents. Not in the VPC.
data "aws_iam_policy_document" "lambda_assistant_producer_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_assistant_cost_snapshot" {
  name               = "${var.project}-lambda-assistant-cost-snapshot-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assistant_producer_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_assistant_cost_snapshot_logs" {
  role       = aws_iam_role.lambda_assistant_cost_snapshot.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_assistant_cost_snapshot" {
  statement {
    sid       = "ReadProjectCost"
    actions   = ["ce:GetCostAndUsage", "ce:GetCostForecast"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadProjectBudget"
    actions   = ["budgets:ViewBudget"]
    resources = ["arn:aws:budgets::${var.account_id}:budget/${aws_budgets_budget.project.name}"]
  }

  statement {
    sid       = "ListCostPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.model_artifacts.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["assistant/admin/cost/*"]
    }
  }

  statement {
    sid       = "ReadWriteCostPrefix"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.model_artifacts.arn}/assistant/admin/cost/*"]
  }
}

resource "aws_iam_role_policy" "lambda_assistant_cost_snapshot" {
  name   = "${var.project}-lambda-assistant-cost-snapshot"
  role   = aws_iam_role.lambda_assistant_cost_snapshot.id
  policy = data.aws_iam_policy_document.lambda_assistant_cost_snapshot.json
}

resource "aws_iam_role" "lambda_assistant_traffic_aggregate" {
  name               = "${var.project}-lambda-assistant-traffic-aggregate-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assistant_producer_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "assistant"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_assistant_traffic_aggregate_logs" {
  role       = aws_iam_role.lambda_assistant_traffic_aggregate.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_assistant_traffic_aggregate" {
  statement {
    sid     = "QueryTrafficLogs"
    actions = ["logs:StartQuery"]
    resources = [
      "${aws_cloudwatch_log_group.waf_cloudfront.arn}:*",
      "${aws_cloudwatch_log_group.waf_api_gateway.arn}:*",
      "${aws_cloudwatch_log_group.api_gateway_access.arn}:*",
    ]
  }

  # GetQueryResults takes a query id, not a log group, so it can't be scoped.
  statement {
    sid       = "ReadQueryResults"
    actions   = ["logs:GetQueryResults"]
    resources = ["*"]
  }

  statement {
    sid       = "ListTrafficPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.model_artifacts.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["assistant/admin/traffic/*"]
    }
  }

  statement {
    sid       = "ReadWriteTrafficPrefix"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.model_artifacts.arn}/assistant/admin/traffic/*"]
  }
}

resource "aws_iam_role_policy" "lambda_assistant_traffic_aggregate" {
  name   = "${var.project}-lambda-assistant-traffic-aggregate"
  role   = aws_iam_role.lambda_assistant_traffic_aggregate.id
  policy = data.aws_iam_policy_document.lambda_assistant_traffic_aggregate.json
}
