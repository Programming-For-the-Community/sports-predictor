# NFL inference Lambda. Triggered by API Gateway -- see
# api-gateway-nfl-predict.tf for the routes, methods, and Cognito
# authorizer wiring. Reads live feature context from DynamoDB and the
# current promoted model from the model artifacts bucket, computes a
# prediction on every request, and audits it to the predictions table.
# See Source/aws-lambdas/nfl/predict/handler.py.
#
# Container image, not the zip packaging ingest/normalize use -- xgboost
# pulls in numpy and scipy, which alone leave almost no headroom under
# Lambda's 250MB unzipped zip limit; container Lambdas get a 10GB image
# limit instead. Code is built and pushed by the nfl_ai_hosting GitHub
# Actions workflow, not Terraform. image_uri references the shared ECR
# repo's floating "-latest" tag, so the URI string itself never changes
# between deploys.
#
# VPC-attached (unlike ingest/normalize) -- this is the one Lambda
# reachable from outside the account, so it stays inside the private
# subnets on the dedicated aws_security_group.lambda_inference
# (security-groups.tf), reaching DynamoDB/S3 only via the VPC Gateway
# Endpoints in vpc-endpoints.tf.
resource "aws_cloudwatch_log_group" "nfl_predict" {
  name              = "/aws/lambda/${var.project}-nfl-predict"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

resource "aws_lambda_function" "nfl_predict" {
  function_name = "${var.project}-nfl-predict"
  description   = "Computes live NFL event-outcome and player-prop predictions on request. Triggered by API Gateway -- see api-gateway-nfl-predict.tf."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:nfl-predict-latest"
  # API Gateway's own REST API integration timeout is a hard,
  # non-configurable 29s ceiling for the API-Gateway-triggered routes, so
  # this can't help or hurt those. Set to 120s for the
  # ScheduledSeasonProjection path (scheduler-nfl-season-projection.tf),
  # which is invoked directly by EventBridge Scheduler, bypasses API
  # Gateway entirely, and needs more than 29s to compute.
  timeout = 120
  # Lambda CPU scales with memory (roughly linear up to ~1,769MB = 1
  # vCPU) -- sized for import/init CPU (xgboost/scikit-learn/pandas), not
  # runtime memory need, which stays well under 512MB even on the
  # heaviest requests.
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
    log_group  = aws_cloudwatch_log_group.nfl_predict.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  tags = merge(local.common_tags, {
    Sport     = "nfl"
    Component = "serving"
  })
}

resource "aws_lambda_permission" "api_gateway_invoke_nfl_predict" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nfl_predict.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
