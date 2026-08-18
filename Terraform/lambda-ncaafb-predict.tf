# NCAAFB inference Lambda -- mirrors lambda-nfl-predict.tf. Reads feature
# context from DynamoDB and the current promoted model from the model
# artifacts bucket. See Source/aws-lambdas/ncaafb/predict/handler.py.
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
  description   = "Computes NCAAFB event-outcome, player-prop, and ranking predictions in the background. Never called by API Gateway -- invoked by EventBridge Scheduler (season projection) or an async invoke from ncaafb_predict_read on a cache miss. See predict/handler.py."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:ncaafb-predict-latest"
  architectures = ["arm64"]
  # Not on the API Gateway request path (fired async). Raised from 300s --
  # NBA's own equivalent (identical shape) needed a real 600s run to
  # finish, confirmed by the user manually bumping it to unblock
  # themselves; matched here for consistency.
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
