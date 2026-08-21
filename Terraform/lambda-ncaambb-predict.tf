# NCAA MBB inference Lambda. Reads feature context from DynamoDB and the
# current promoted model from the model artifacts bucket.
#
# Container image (xgboost/lightgbm dependency footprint). Built/pushed by
# the ncaambb_ai_hosting workflow. VPC-attached.
resource "aws_cloudwatch_log_group" "ncaambb_predict" {
  name              = "/aws/lambda/${var.project}-ncaambb-predict"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "serving"
  })
}

resource "aws_lambda_function" "ncaambb_predict" {
  function_name = "${var.project}-ncaambb-predict"
  description   = "Computes NCAA MBB event-outcome and player-prop predictions in the background. Never called by API Gateway -- invoked async from ncaambb_predict_read on a cache miss. See predict/handler.py."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:ncaambb-predict-latest"
  architectures = ["arm64"]
  # Not on the API Gateway request path (fired async). Sized to match
  # nba_predict -- D1's larger team/roster volume (~362 teams vs. NBA's
  # 30) makes a smaller budget riskier here, not safer.
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
    log_group  = aws_cloudwatch_log_group.ncaambb_predict.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_ncaambb_predict" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ncaambb_predict.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
