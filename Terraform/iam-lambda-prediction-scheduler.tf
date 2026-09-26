# prediction-scheduler's own role: reads upcoming events (events table's
# sport-status-index), reads/writes its own claim markers and the snapshot
# existence check (predictions table), and async-invokes each head-to-head
# sport's ingest (pre-kickoff refresh) and predict (snapshot) Lambdas (PGA and
# F1: predict only -- their start-of-event snapshot has no ingest refresh).
# Not in the VPC, so it needs no network permissions.
data "aws_iam_policy_document" "lambda_prediction_scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_prediction_scheduler" {
  name               = "${var.project}-lambda-prediction-scheduler-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_prediction_scheduler_assume.json

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "prediction"
  })
}

resource "aws_iam_role_policy_attachment" "lambda_prediction_scheduler_logs" {
  role       = aws_iam_role.lambda_prediction_scheduler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_prediction_scheduler" {
  statement {
    sid       = "ReadUpcomingEvents"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.events.arn}/index/sport-status-index"]
  }

  statement {
    sid       = "PredictionsMarkersAndSnapshotCheck"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.predictions.arn]
  }

  statement {
    sid     = "InvokeIngestAndPredict"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.nfl_ingest.arn,
      aws_lambda_function.nba_ingest.arn,
      aws_lambda_function.ncaambb_ingest.arn,
      aws_lambda_function.nfl_predict.arn,
      aws_lambda_function.ncaafb_predict.arn,
      aws_lambda_function.nba_predict.arn,
      aws_lambda_function.ncaambb_predict.arn,
      aws_lambda_function.pga_predict.arn,
      aws_lambda_function.f1_predict.arn,
    ]
  }
}

resource "aws_iam_role_policy" "lambda_prediction_scheduler" {
  name   = "${var.project}-lambda-prediction-scheduler"
  role   = aws_iam_role.lambda_prediction_scheduler.id
  policy = data.aws_iam_policy_document.lambda_prediction_scheduler.json
}
