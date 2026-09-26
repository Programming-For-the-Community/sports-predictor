# model-performance's own role: read-only on the four DynamoDB tables
# FeatureStorage wraps plus the predictions table, read on the model-artifacts
# bucket (model cards), and write to just the model-performance/ prefix of it.
# Not in the VPC, so it needs no network permissions.
data "aws_iam_policy_document" "lambda_model_performance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_model_performance" {
  name               = "${var.project}-lambda-model-performance-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_model_performance_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "prediction"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_model_performance_logs" {
  role       = aws_iam_role.lambda_model_performance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_model_performance" {
  statement {
    sid     = "ReadTables"
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:BatchGetItem"]
    resources = [
      aws_dynamodb_table.events.arn,
      "${aws_dynamodb_table.events.arn}/index/*",
      aws_dynamodb_table.entities.arn,
      "${aws_dynamodb_table.entities.arn}/index/*",
      aws_dynamodb_table.player_game_stats.arn,
      "${aws_dynamodb_table.player_game_stats.arn}/index/*",
      aws_dynamodb_table.team_game_stats.arn,
      "${aws_dynamodb_table.team_game_stats.arn}/index/*",
      aws_dynamodb_table.predictions.arn,
    ]
  }

  statement {
    sid       = "ReadModelCards"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.model_artifacts.arn, "${aws_s3_bucket.model_artifacts.arn}/*"]
  }

  statement {
    sid       = "WriteScorecards"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.model_artifacts.arn}/model-performance/*"]
  }
}

resource "aws_iam_role_policy" "lambda_model_performance" {
  name   = "${var.project}-lambda-model-performance"
  role   = aws_iam_role.lambda_model_performance.id
  policy = data.aws_iam_policy_document.lambda_model_performance.json
}
