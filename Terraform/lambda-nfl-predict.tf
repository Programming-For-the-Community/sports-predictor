# NFL inference Lambda. Not triggered by API Gateway directly -- runs in
# the background via EventBridge Scheduler or an async invoke from
# nfl_predict_read. Reads live feature context from DynamoDB and the
# current promoted model, computes a prediction, and writes it to the
# predictions table. See Source/aws-lambdas/nfl/predict/handler.py.
#
# Container image -- xgboost pulls in numpy/scipy, which leave no headroom
# under Lambda's zip size limit; container Lambdas get a 10GB image limit
# instead. Built and pushed by the nfl_ai_hosting GitHub Actions workflow,
# not Terraform. image_uri references the shared ECR repo's floating
# "-latest" tag, so the URI string itself never changes between deploys.
#
# VPC-attached -- reachable from outside the account, stays inside the
# private subnets on aws_security_group.lambda_inference
# (security-groups.tf), reaching DynamoDB/S3 via the VPC Gateway
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
  description   = "Computes NFL event-outcome and player-prop predictions in the background. Never called by API Gateway -- invoked by EventBridge Scheduler (season projection) or an async invoke from nfl_predict_read on a cache miss. See predict/handler.py."
  role          = aws_iam_role.lambda_inference.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:nfl-predict-latest"
  # Graviton (arm64) -- better price/performance for inference. Image is
  # built for arm64 by nfl_ai_hosting.yml's docker_build_push (platform:
  # linux/arm64); a mismatch between this setting and the pushed image's
  # platform fails at invoke time, not at `terraform apply`.
  architectures = ["arm64"]
  # Not on the API Gateway request path (invoked async), so this only
  # bounds how long a scheduled/background prediction or season-projection
  # run gets.
  timeout = 600
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
