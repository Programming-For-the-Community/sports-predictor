# Mirrors iam-lambda-live-scores.tf (NFL's) -- a dedicated per-sport role
# rather than widening that one, since its own permissions are scoped to
# NFL's raw-bucket cache prefix specifically. NCAAFB's live-scores Lambda
# calls ESPN's supplemental scoreboard client (zero CFBD quota, per
# design/DATA_SOURCES.md), so this role needs no secretsmanager access --
# unlike iam-ncaafb-backfill.tf, there's no keyed API in this Lambda's path.
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
  # Same FeatureStorage-constructor rationale as iam-lambda-live-scores.tf's
  # own comment -- only the events table + its status-index GSI are
  # actually queried.
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
