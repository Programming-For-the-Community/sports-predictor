# Kept separate from the ingest/normalize role in iam-lambda-pipeline.tf --
# this is the one function reachable (via API Gateway) from outside the
# account, so it shouldn't carry that role's write access to the raw bucket
# and the entity/event/stats tables. Its own access is read-only on
# everything except the predictions table it writes.
data "aws_iam_policy_document" "lambda_inference_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_inference" {
  name               = "${var.project}-lambda-inference-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_inference_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_inference_logs" {
  role       = aws_iam_role.lambda_inference.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Required because this Lambda is VPC-attached (lambda-nfl-predict.tf's
# vpc_config) -- unlike lambda_pipeline (ingest/normalize's role), which
# never needs this since those functions have no vpc_config block at all.
# Lambda provisions an ENI per subnet/security-group combination to reach
# the private subnets, which needs ec2:CreateNetworkInterface and friends
# on the execution role -- this AWS-managed policy grants exactly that.
resource "aws_iam_role_policy_attachment" "lambda_inference_vpc_access" {
  role       = aws_iam_role.lambda_inference.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "lambda_inference_permissions" {
  statement {
    sid       = "ReadModelArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/*"]
  }

  statement {
    sid       = "ListModelArtifacts"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}"]
  }

  # Scoped to the season-projections/ prefix only, not the whole bucket --
  # this Lambda's ScheduledSeasonProjection branch (predict/handler.py)
  # writes the cached season projection there; everything else under this
  # bucket (versioned model artifacts) stays write-protected from this
  # role, written only by the training Fargate task (iam-ecs-pipeline.tf).
  statement {
    sid       = "WriteSeasonProjection"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/season-projections/*"]
  }

  statement {
    sid     = "ReadFeatureData"
    actions = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}",
      # A table's GSI is a distinct IAM resource from the table itself --
      # Query against status-index/entity-history was silently AccessDenied
      # even though the base-table ARNs above were already granted (confirmed
      # live via CloudWatch: get_player_game_stats's entity-history Query
      # threw AccessDeniedException in production despite this same statement
      # already covering GetItem/Query/Scan on the base player_game_stats
      # table). Only these two tables have a GSI today (dynamodb-events.tf's
      # status-index, dynamodb-player-game-stats.tf's entity-history).
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}/index/*",
    ]
  }

  statement {
    sid = "ReadWritePredictions"
    # Query added alongside PutItem -- GET /nfl/events?status=completed now
    # reads back each event's own logged prediction (via the event_key
    # partition key) to show predicted-vs-actual, not just write new rows.
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = ["arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.predictions_table}"]
  }
}

resource "aws_iam_role_policy" "lambda_inference_permissions" {
  name   = "${var.project}-lambda-inference-permissions"
  role   = aws_iam_role.lambda_inference.id
  policy = data.aws_iam_policy_document.lambda_inference_permissions.json
}
