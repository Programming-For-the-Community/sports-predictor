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

data "aws_iam_policy_document" "lambda_inference_permissions" {
  statement {
    sid       = "ReadModelArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.model_artifacts_bucket}/*"]
  }

  statement {
    sid     = "ReadFeatureData"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.entities_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.events_table}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.player_game_stats_table}",
    ]
  }

  statement {
    sid       = "WritePredictions"
    actions   = ["dynamodb:PutItem"]
    resources = ["arn:aws:dynamodb:${var.region}:${var.account_id}:table/${local.predictions_table}"]
  }
}

resource "aws_iam_role_policy" "lambda_inference_permissions" {
  name   = "${var.project}-lambda-inference-permissions"
  role   = aws_iam_role.lambda_inference.id
  policy = data.aws_iam_policy_document.lambda_inference_permissions.json
}
