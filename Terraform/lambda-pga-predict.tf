# PGA inference Lambda. Reads feature context from DynamoDB (a golfer's
# own rolling history lives directly in events.participants, no separate
# player_game_stats table for a field-event sport -- see
# aws-lambdas/pga/predict/live_features.py's own docstring), season-stats
# snapshots from the raw bucket, and the current promoted model from the
# model artifacts bucket.
#
# Container image (xgboost/lightgbm dependency footprint). Built/pushed by
# the pga_ai_hosting workflow. VPC-attached.
resource "aws_cloudwatch_log_group" "pga_predict" {
  name              = "/aws/lambda/${var.project}-pga-predict"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "serving"
  })
}

resource "aws_lambda_function" "pga_predict" {
  function_name = "${var.project}-pga-predict"
  description   = "Computes PGA field/match-play/cup predictions in the background. Never called by API Gateway -- invoked by an async invoke from pga_predict_read on a cache miss. See predict/handler.py."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:pga-predict-latest"
  architectures = ["arm64"]
  # Not on the API Gateway request path (fired async). No season-
  # simulation equivalent the way NBA/NCAAFB's own predict Lambda has
  # (PGA has no season-long standings/odds concept) -- starting narrower
  # than their 600s, tune from real measured invocation duration once it
  # exists to measure (a ~150-golfer field scored against up to 4 models
  # each, plus threaded-free in-memory history resolution -- see
  # live_features.py's own docstring for why no ThreadPoolExecutor is
  # needed there).
  timeout = 120

  memory_size = 3008

  environment {
    variables = {
      MODEL_ARTIFACTS_BUCKET_NAME  = aws_s3_bucket.model_artifacts.bucket
      PREDICTIONS_TABLE_NAME       = aws_dynamodb_table.predictions.name
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
      # library.storage.pga_season_stats' own source -- see
      # iam-lambda-inference.tf's ReadPgaSeasonStatsSnapshots statement.
      RAW_BUCKET_NAME = aws_s3_bucket.raw_data_lake.bucket
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id, aws_subnet.private_c.id]
    security_group_ids = [aws_security_group.lambda_inference.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.pga_predict.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  tags = merge(local.common_tags, {
    Sport     = "pga"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_pga_predict" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pga_predict.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
