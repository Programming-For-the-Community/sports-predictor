# NCAAFB inference Lambda -- mirrors lambda-nfl-predict.tf exactly. No
# CFBD calls of its own: reads feature context already ingested into
# DynamoDB and the current promoted model from the model artifacts bucket,
# same as NFL. See Source/aws-lambdas/ncaafb/predict/handler.py, which also
# adds the ranking-prediction route NFL has no equivalent of.
#
# Container image (xgboost dependency footprint, same rationale as
# lambda-nfl-predict.tf). Built/pushed by the ncaafb_ai_hosting workflow.
# VPC-attached, same shared security group/subnets as the NFL predict
# Lambda.
resource "aws_cloudwatch_log_group" "ncaafb_predict" {
  name              = "/aws/lambda/${var.project}-ncaafb-predict"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

resource "aws_lambda_function" "ncaafb_predict" {
  function_name = "${var.project}-ncaafb-predict"
  description   = "Computes live NCAAFB event-outcome, player-prop, and national-ranking predictions on request. Triggered by API Gateway -- see api-gateway-ncaafb-predict.tf."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:ncaafb-predict-latest"
  architectures = ["arm64"]
  # Same 120s ceiling as lambda-nfl-predict.tf's ScheduledSeasonProjection
  # path -- API Gateway's own 29s integration ceiling still applies to the
  # API-Gateway-triggered routes regardless of this setting.
  timeout     = 120
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
    log_group  = aws_cloudwatch_log_group.ncaafb_predict.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_ncaafb_predict" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ncaafb_predict.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
