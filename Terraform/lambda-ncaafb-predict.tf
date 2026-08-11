# NCAAFB inference Lambda -- mirrors lambda-nfl-predict.tf exactly. No
# CFBD calls of its own: reads feature context already ingested into
# DynamoDB and the current promoted model from the model artifacts bucket,
# same as NFL. See Source/aws-lambdas/ncaafb/predict/handler.py, which
# also runs the season-long national-ranking simulation NFL has no
# equivalent of (season_projection.py/season_simulation.py).
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
  description   = "Computes NCAAFB event-outcome, player-prop, and national-ranking predictions in the background -- never triggered by API Gateway directly (see api-gateway-ncaafb-predict.tf, which points every GET route at ncaafb_predict_read instead). Invoked by an EventBridge Scheduler (season projection) or a fire-and-forget async invoke from ncaafb_predict_read on a prediction-cache miss (see predict/handler.py's own docstring)."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:ncaafb-predict-latest"
  architectures = ["arm64"]
  # No API Gateway ceiling to respect anymore -- this Lambda is never on
  # that request path (see the description above), so its own timeout is
  # the only limit that matters. 5 minutes covers a slow season
  # simulation (season_projection.py's Monte Carlo loop, scored against
  # the real national-ranking model every iteration -- see
  # season_simulation.py's own docstring) with real headroom.
  timeout     = 300
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
