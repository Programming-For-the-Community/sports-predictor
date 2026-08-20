# Dedicated, minimal role -- season-gate does pure date math on its event
# payload only, no AWS API calls of its own, so CloudWatch Logs via the
# AWS-managed basic execution policy is the entire permission set it needs.
data "aws_iam_policy_document" "lambda_season_gate_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_season_gate" {
  name               = "${var.project}-lambda-season-gate-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_season_gate_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_season_gate_logs" {
  role       = aws_iam_role.lambda_season_gate.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
