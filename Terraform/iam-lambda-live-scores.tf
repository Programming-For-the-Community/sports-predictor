# Read-only on the events table; read+write limited to the live-scores
# cache prefix of the raw bucket.
data "aws_iam_policy_document" "lambda_live_scores_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_live_scores" {
  name               = "${var.project}-lambda-live-scores-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_live_scores_assume.json

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_live_scores_logs" {
  role       = aws_iam_role.lambda_live_scores.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_live_scores_permissions" {
  # live_scores.py only ever calls get_all_events, so only the events
  # table + its status-index GSI need a grant here.
  statement {
    sid     = "ReadEvents"
    actions = ["dynamodb:Query"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
    ]
  }

  # Scoped to the live-scores cache prefix only, not the whole raw bucket.
  statement {
    sid       = "ReadWriteLiveScoresCache"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/nfl/cache/live-scores/*"]
  }
}

resource "aws_iam_role_policy" "lambda_live_scores_permissions" {
  name   = "${var.project}-lambda-live-scores-permissions"
  role   = aws_iam_role.lambda_live_scores.id
  policy = data.aws_iam_policy_document.lambda_live_scores_permissions.json
}
