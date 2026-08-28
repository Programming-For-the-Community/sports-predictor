# Dedicated per-sport role scoped to PGA's own raw-bucket cache prefix.
# PGA's live-scores Lambda calls ESPN directly (keyless), so this role
# needs no secretsmanager access.
data "aws_iam_policy_document" "pga_live_scores_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pga_live_scores" {
  name               = "${var.project}-pga-live-scores-exec"
  assume_role_policy = data.aws_iam_policy_document.pga_live_scores_assume.json

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "serving"
  })
}

resource "aws_iam_role_policy_attachment" "pga_live_scores_logs" {
  role       = aws_iam_role.pga_live_scores.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "pga_live_scores_permissions" {
  # Only the events table and its sport-status-index GSI are queried.
  statement {
    sid     = "ReadEvents"
    actions = ["dynamodb:Query"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
    ]
  }

  statement {
    sid       = "ReadWriteLiveScoresCache"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/pga/cache/live-scores/*"]
  }
}

resource "aws_iam_role_policy" "pga_live_scores_permissions" {
  name   = "${var.project}-pga-live-scores-permissions"
  role   = aws_iam_role.pga_live_scores.id
  policy = data.aws_iam_policy_document.pga_live_scores_permissions.json
}
