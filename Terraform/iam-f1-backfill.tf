# Used as both the task execution role (ECR pull, log writes) and the
# task role (the application code's own AWS access) for
# Source/data-backfills/f1. Write-only on the raw data lake and the
# entities/events DynamoDB tables -- unlike every head-to-head sport's
# backfill role, there's no player_game_stats/team_game_stats grant here:
# a field-event sport's results already live entirely in
# events.participants (design/DATA_SCHEMA.md), so F1's backfill never
# calls write_player_game_stats/write_team_game_stats. No secretsmanager
# grant -- Jolpica is keyless.
data "aws_iam_policy_document" "f1_backfill_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "f1_backfill" {
  name               = "${var.project}-f1-backfill-role"
  assume_role_policy = data.aws_iam_policy_document.f1_backfill_assume.json

  tags = merge(local.common_tags, {
    Sport     = "f1"
    Component = "ingestion"
  })
}

resource "aws_iam_role_policy_attachment" "f1_backfill_execution" {
  role       = aws_iam_role.f1_backfill.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "f1_backfill_permissions" {
  statement {
    sid       = "WriteRawDataLake"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.raw_data_lake.arn}/f1/*"]
  }

  # HeadObject is authorized by s3:GetObject above, but without
  # s3:ListBucket too, S3 can't tell "object doesn't exist" from "not
  # allowed to know" and returns 403 instead of 404. Scoped to the f1/
  # prefix so this role still can't enumerate other sports' data.
  statement {
    sid       = "ListRawDataLakeF1Prefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw_data_lake.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["f1/*"]
    }
  }

  statement {
    sid       = "WriteEntitiesAndEvents"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.entities.arn, aws_dynamodb_table.events.arn]
  }
}

resource "aws_iam_role_policy" "f1_backfill_permissions" {
  name   = "${var.project}-f1-backfill-permissions"
  role   = aws_iam_role.f1_backfill.id
  policy = data.aws_iam_policy_document.f1_backfill_permissions.json
}
