# Shared by every sport's ingest and normalize Lambdas -- they run
# sequentially with no security-meaningful boundary between them, so one
# role covers both. Not split per-sport either: DynamoDB's LeadingKeys
# condition only supports exact partition-key matches, not the
# SPORT#<sport># prefix this schema uses, so per-sport IAM scoping isn't
# enforceable here.
data "aws_iam_policy_document" "lambda_pipeline_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_pipeline" {
  name               = "${var.project}-lambda-pipeline-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_pipeline_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "ingestion"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_pipeline_logs" {
  role       = aws_iam_role.lambda_pipeline.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_pipeline_permissions" {
  statement {
    sid       = "RawBucketReadWrite"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/*"]
  }

  statement {
    sid     = "WriteNormalizedData"
    actions = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}",
      # A table's GSI is a distinct IAM resource from the table itself --
      # needed for PipelineStorage.get_events_by_status's sport-status-index
      # Query.
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
    ]
  }

  # Read access to the shared third-party-API-key secret -- only NCAAFB's
  # ingest and schedule-sync Lambdas call a keyed API (CFBD); normalize
  # never does, and NFL's ESPN endpoints are keyless. The secret's
  # per-field access boundary (CFBD_API_KEY_SECRET_FIELD) is
  # application-level only.
  statement {
    sid       = "ReadThirdPartyApiKeySecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.third_party_api_key_secret_arn]
  }
}

resource "aws_iam_role_policy" "lambda_pipeline_permissions" {
  name   = "${var.project}-lambda-pipeline-permissions"
  role   = aws_iam_role.lambda_pipeline.id
  policy = data.aws_iam_policy_document.lambda_pipeline_permissions.json
}
