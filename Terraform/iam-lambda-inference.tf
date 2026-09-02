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

# This Lambda is VPC-attached (lambda-nfl-predict.tf's vpc_config), which
# needs ec2:CreateNetworkInterface and friends to provision an ENI per
# subnet/security-group -- this AWS-managed policy grants exactly that.
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
    sid = "ReadFeatureData"
    # BatchGetItem -- library.serving.common.prefetch_entities' own batched
    # entity lookup (DynamoDBTable.batch_get_items), called from every
    # sport's own list_events. Missing here since that path was added,
    # confirmed a real production 500 (AccessDeniedException) 2026-09-02 --
    # it only started firing once list_events' own get_all_events query
    # was bounded (this same session's fix) and actually finished fast
    # enough to reach prefetch_entities at all; before that it reliably
    # hit its own 29s Lambda timeout first, so this gap was never exercised.
    actions = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}",
      # A table's GSI is a distinct IAM resource from the table itself --
      # each Query'd GSI needs its own /index/* entry alongside the base
      # table ARN. team_game_stats_table/index/* covers get_all_team_game_stats,
      # reached from this role's live-inference path via
      # live_features.py's get_team_game_stats_for_team. entities_table/index/*
      # covers get_team_entities (team-index), live_features.py's
      # roster-driven presumptive-leader candidate selection.
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}/index/*",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}/index/*",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}/index/*",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.team_game_stats_table}/index/*",
    ]
  }

  statement {
    sid = "ReadWritePredictions"
    # Query lets GET /nfl/events?status=completed read back each event's
    # logged prediction (via event_key) to show predicted-vs-actual.
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = ["arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.predictions_table}"]
  }

  # library.storage.prediction_cache's cache entries and in-progress
  # markers. GetObject is already covered by ReadModelArtifacts above.
  statement {
    sid       = "ReadWritePredictionCache"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/predictions-cache/*"]
  }

  # ncaambb_predict's own season_projection.py reads schedule-sync's
  # daily-refreshed conference-membership cache from the RAW bucket
  # (a different bucket from every other statement here) instead of
  # calling ESPN live -- this Lambda has no route to the public internet
  # at all (no NAT Gateway; its security group only opens 443 to the
  # S3/DynamoDB VPC Gateway Endpoints' own prefix lists, which this read
  # still travels over since it's still S3 traffic). Scoped to that one
  # prefix, not the whole raw bucket -- every other sport's predict
  # Lambda shares this role but has no reason to ever read it.
  statement {
    sid       = "ReadConferenceMembershipCache"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/ncaambb/conference-membership/*"]
  }

  # pga_predict's own live_features.py resolves each golfer's season-stats
  # block (driving distance/accuracy, GIR%, etc.) the same way feature-
  # engineering/pga/build_dataset.py does at training time -- via
  # library.storage.pga_season_stats reading pga-ingest's own daily raw
  # snapshot directly from the raw bucket. Same "scoped to one sport's own
  # prefix, not the whole raw bucket" pattern ReadConferenceMembershipCache
  # above already uses.
  statement {
    sid       = "ReadPgaSeasonStatsSnapshots"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.raw_bucket_name}/pga/statistics/*"]
  }

  # predict-read's async invoke of predict on a prediction-cache miss
  # (library.aws.lambda_invoker).
  statement {
    sid     = "InvokePredictLambda"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.nfl_predict.arn, aws_lambda_function.ncaafb_predict.arn,
      aws_lambda_function.nba_predict.arn, aws_lambda_function.ncaambb_predict.arn,
      aws_lambda_function.pga_predict.arn, aws_lambda_function.f1_predict.arn,
    ]
  }
}

resource "aws_iam_role_policy" "lambda_inference_permissions" {
  name   = "${var.project}-lambda-inference-permissions"
  role   = aws_iam_role.lambda_inference.id
  policy = data.aws_iam_policy_document.lambda_inference_permissions.json
}
