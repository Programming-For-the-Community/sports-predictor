# NBA inference Lambda. Reads feature context from DynamoDB and the current
# promoted model from the model artifacts bucket.
#
# Container image (xgboost/lightgbm dependency footprint). Built/pushed by
# the nba_ai_hosting workflow. VPC-attached.
resource "aws_cloudwatch_log_group" "nba_predict" {
  name              = "/aws/lambda/${var.project}-nba-predict"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "serving"
  })
}

resource "aws_lambda_function" "nba_predict" {
  function_name = "${var.project}-nba-predict"
  description   = "Computes NBA event-outcome and player-prop predictions in the background. Never called by API Gateway -- invoked by EventBridge Scheduler (season projection) or an async invoke from nba_predict_read on a cache miss. See predict/handler.py."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:nba-predict-latest"
  architectures = ["arm64"]
  # Not on the API Gateway request path (fired async). Long enough for a
  # full NBA season-projection run (play-in + bracket + cup) to finish.
  timeout = 600

  memory_size = 3008

  environment {
    variables = {
      MODEL_ARTIFACTS_BUCKET_NAME  = aws_s3_bucket.model_artifacts.bucket
      PREDICTIONS_TABLE_NAME       = aws_dynamodb_table.predictions.name
      ENTITIES_TABLE_NAME          = aws_dynamodb_table.entities.name
      EVENTS_TABLE_NAME            = aws_dynamodb_table.events.name
      PLAYER_GAME_STATS_TABLE_NAME = aws_dynamodb_table.player_game_stats.name
      TEAM_GAME_STATS_TABLE_NAME   = aws_dynamodb_table.team_game_stats.name
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id, aws_subnet.private_c.id]
    security_group_ids = [aws_security_group.lambda_inference.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.nba_predict.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  tags = merge(local.common_tags, {
    Sport     = "nba"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_nba_predict" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nba_predict.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
