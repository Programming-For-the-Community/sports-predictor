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
    sid     = "ReadFeatureData"
    actions = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
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

  # library.storage.prediction_cache's own on-demand prediction cache --
  # both predict-read (reads a cache entry, writes/deletes its own
  # in-progress claim marker) and predict (writes the real result once
  # computed, clears the claim) share this same role, so one statement
  # covers both sides. GetObject is already covered by ReadModelArtifacts'
  # whole-bucket grant above; this adds the write/delete half, scoped to
  # the predictions-cache/ prefix only -- same reasoning
  # WriteSeasonProjection's own scoping comment gives for not opening up
  # the rest of the bucket (versioned model artifacts stay write-
  # protected, written only by the training Fargate task).
  statement {
    sid       = "ReadWritePredictionCache"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/predictions-cache/*"]
  }

  # predict-read's own trigger for a prediction-cache miss/stale-refresh
  # (library.aws.lambda_invoker.LambdaInvoker) -- an async, fire-and-
  # forget invoke of the heavy predict Lambda, for either sport. Self-
  # invoke (predict invoking itself) is unused but harmless -- both
  # Lambdas share this one role, so there's no way to grant the cross-
  # invoke half without the self half coming along with it.
  statement {
    sid       = "InvokePredictLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.nfl_predict.arn, aws_lambda_function.ncaafb_predict.arn]
  }
}

resource "aws_iam_role_policy" "lambda_inference_permissions" {
  name   = "${var.project}-lambda-inference-permissions"
  role   = aws_iam_role.lambda_inference.id
  policy = data.aws_iam_policy_document.lambda_inference_permissions.json
}
