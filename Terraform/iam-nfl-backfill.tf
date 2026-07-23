# Used as both the task execution role (ECR pull, log writes -- via the
# attached AWS-managed policy) and the task role (the application code's
# own AWS access), same combined-role pattern as iam-ecs-pipeline.tf.
# Scoped only to what Source/data-backfills/nfl actually calls: write raw
# JSON to the raw data lake, upsert the three DynamoDB tables. No read
# access -- the backfill job doesn't need to read anything back out.
data "aws_iam_policy_document" "nfl_backfill_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "nfl_backfill" {
  name               = "${var.project}-nfl-backfill-role"
  assume_role_policy = data.aws_iam_policy_document.nfl_backfill_assume.json

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "ingestion"
  })
}

resource "aws_iam_role_policy_attachment" "nfl_backfill_execution" {
  role       = aws_iam_role.nfl_backfill.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "nfl_backfill_permissions" {
  statement {
    sid       = "WriteRawDataLake"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.raw_data_lake.arn}/nfl/*"]
  }

  # HeadObject (used by raw_object_exists() to skip already-loaded games)
  # is authorized by s3:GetObject above, but without s3:ListBucket too, S3
  # can't tell "object doesn't exist" from "not allowed to know" and
  # returns 403 instead of 404 for a missing key -- which is almost every
  # key on a first run. Scoped to the nfl/ prefix via the condition so
  # this role still can't enumerate other sports' data in the same bucket.
  statement {
    sid       = "ListRawDataLakeNflPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw_data_lake.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["nfl/*"]
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
}

resource "aws_iam_role_policy" "nfl_backfill_permissions" {
  name   = "${var.project}-nfl-backfill-permissions"
  role   = aws_iam_role.nfl_backfill.id
  policy = data.aws_iam_policy_document.nfl_backfill_permissions.json
}
