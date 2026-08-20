# Dedicated per-sport role scoped to NCAAFB's own raw-bucket cache
# prefix. NCAAFB's live-scores Lambda calls ESPN's supplemental
# scoreboard client (zero CFBD quota), so this role needs no
# secretsmanager access.
data "aws_iam_policy_document" "ncaafb_live_scores_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ncaafb_live_scores" {
  name               = "${var.project}-ncaafb-live-scores-exec"
  assume_role_policy = data.aws_iam_policy_document.ncaafb_live_scores_assume.json

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

resource "aws_iam_role_policy_attachment" "ncaafb_live_scores_logs" {
  role       = aws_iam_role.ncaafb_live_scores.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ncaafb_live_scores_permissions" {
  # Only the events table and its status-index GSI are queried.
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
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/ncaafb/cache/live-scores/*"]
  }
}

resource "aws_iam_role_policy" "ncaafb_live_scores_permissions" {
  name   = "${var.project}-ncaafb-live-scores-permissions"
  role   = aws_iam_role.ncaafb_live_scores.id
  policy = data.aws_iam_policy_document.ncaafb_live_scores_permissions.json
}
