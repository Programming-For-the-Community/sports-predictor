# Dedicated per-sport role scoped to F1's own raw-bucket cache prefix.
# F1's live-scores Lambda calls ESPN directly (keyless), so this role
# needs no secretsmanager access.
data "aws_iam_policy_document" "f1_live_scores_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "f1_live_scores" {
  name               = "${var.project}-f1-live-scores-exec"
  assume_role_policy = data.aws_iam_policy_document.f1_live_scores_assume.json

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "serving"
  })
}

resource "aws_iam_role_policy_attachment" "f1_live_scores_logs" {
  role       = aws_iam_role.f1_live_scores.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "f1_live_scores_permissions" {
  # Only the events table and its sport-status-index GSI are queried --
  # both the current-roster resolution and the event-id-by-date join
  # (library/normalize/f1.py's own schedule stub events).
  statement {
    sid     = "ReadEvents"
    actions = ["dynamodb:Query"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
    ]
  }

  # _current_roster_by_name resolves each driver's own real name via a
  # direct GetItem, not a Query.
  statement {
    sid       = "ReadEntities"
    actions   = ["dynamodb:GetItem"]
    resources = ["arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}"]
  }

  statement {
    sid       = "ReadWriteLiveScoresCache"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/f1/cache/live-scores/*"]
  }
}

resource "aws_iam_role_policy" "f1_live_scores_permissions" {
  name   = "${var.project}-f1-live-scores-permissions"
  role   = aws_iam_role.f1_live_scores.id
  policy = data.aws_iam_policy_document.f1_live_scores_permissions.json
}
