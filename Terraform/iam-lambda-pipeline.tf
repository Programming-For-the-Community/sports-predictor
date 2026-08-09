# Shared by every sport's ingest and normalize Lambdas. These two steps run
# sequentially in the same pipeline with no security-meaningful boundary
# between them, so one role for both keeps the role count down without
# giving up anything for a single-user project. Not split per-sport either:
# DynamoDB's LeadingKeys condition only supports exact partition-key
# matches, not the SPORT#<sport># prefix this schema uses, so per-sport IAM
# scoping isn't enforceable here -- per-sport cost visibility already comes
# from the Sport tag (see docs/TAGGING_STRATEGY.md), not from IAM.
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
    ]
  }

  # Read access to the one shared third-party-API-key secret (see
  # variables.tf's third_party_api_key_secret_arn) -- only NCAAFB's ingest
  # and schedule-sync Lambdas actually call a keyed API (CFBD) today;
  # normalize never does (it only transforms raw JSON already in S3), and
  # NFL's ESPN endpoints are keyless. Granted at this shared role level
  # rather than split out because DynamoDB's own per-sport IAM scoping
  # already isn't enforceable here (see this file's top comment) -- the
  # secret's per-field access boundary is application-level only (each
  # task reads CFBD_API_KEY_SECRET_FIELD), same accepted tradeoff as the
  # secret's own design.
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
