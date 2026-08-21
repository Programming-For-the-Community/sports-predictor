# Used as both the task execution role (ECR pull, log writes) and the
# task role (the application code's own AWS access) for
# Source/data-backfills/ncaambb. Write-only on the raw data lake and the
# four DynamoDB tables. No secretsmanager grant -- ESPN is keyless.
data "aws_iam_policy_document" "ncaambb_backfill_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ncaambb_backfill" {
  name               = "${var.project}-ncaambb-backfill-role"
  assume_role_policy = data.aws_iam_policy_document.ncaambb_backfill_assume.json

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "ingestion"
  })
}

resource "aws_iam_role_policy_attachment" "ncaambb_backfill_execution" {
  role       = aws_iam_role.ncaambb_backfill.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ncaambb_backfill_permissions" {
  statement {
    sid       = "WriteRawDataLake"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.raw_data_lake.arn}/ncaambb/*"]
  }

  # HeadObject is authorized by s3:GetObject above, but without
  # s3:ListBucket too, S3 can't tell "object doesn't exist" from "not
  # allowed to know" and returns 403 instead of 404. Scoped to the
  # ncaambb/ prefix so this role still can't enumerate other sports' data.
  statement {
    sid       = "ListRawDataLakeNcaambbPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw_data_lake.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["ncaambb/*"]
    }
  }

  statement {
    sid       = "WriteEntitiesAndEvents"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.entities.arn, aws_dynamodb_table.events.arn]
  }

  statement {
    sid       = "WritePlayerGameStats"
    actions   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem"]
    resources = [aws_dynamodb_table.player_game_stats.arn]
  }

  statement {
    sid       = "WriteTeamGameStats"
    actions   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem"]
    resources = [aws_dynamodb_table.team_game_stats.arn]
  }
}

resource "aws_iam_role_policy" "ncaambb_backfill_permissions" {
  name   = "${var.project}-ncaambb-backfill-permissions"
  role   = aws_iam_role.ncaambb_backfill.id
  policy = data.aws_iam_policy_document.ncaambb_backfill_permissions.json
}
