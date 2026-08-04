# NFL inference Lambda. Triggered by API Gateway -- see
# api-gateway-nfl-predict.tf for the routes, methods, and Cognito
# authorizer wiring. Reads live feature context from DynamoDB and the
# current promoted model from the model artifacts bucket, computes a
# prediction on every request, and audits it to the predictions table.
# See Source/aws-lambdas/nfl/predict/handler.py.
#
# Container image, not the zip packaging ingest/normalize use (see
# lambda-nfl-ingest.tf) -- xgboost pulls in numpy and scipy, which alone
# measure ~225MB unzipped for this runtime, leaving almost no headroom
# under Lambda's 250MB unzipped zip limit. Container Lambdas get a 10GB
# image limit instead. Code is built and pushed by the nfl_ai_hosting
# GitHub Actions workflow -- NOT by Terraform.
#
# image_uri references the shared ECR repo's floating "-latest" tag for
# this component, same convention ECS task definitions use (see
# ecs-task-nfl-train-model.tf) -- a floating tag means the URI string
# itself never changes between deploys, so (unlike the zip Lambdas'
# placeholder/ignore_changes dance) there's nothing here for a later
# `terraform apply` to fight with. The one thing this DOESN'T solve on
# its own: Lambda's CreateFunction API validates the image exists in ECR
# at creation time (ECS task definitions don't -- they only check at
# run-task). nfl_deploy.yml handles this by running nfl_ai_hosting's
# build+push BEFORE tf_install's apply, so the tag always already exists
# by the time this resource is created -- see nfl_deploy.yml's own
# top-of-file comment for the full ordering and the one path
# (tf_install.yml run standalone, outside nfl_deploy.yml, against a
# never-before-deployed account) that ordering doesn't cover.
#
# VPC-attached (unlike ingest/normalize) -- this is the one Lambda
# reachable from outside the account, so it stays inside the private
# subnets on the dedicated aws_security_group.lambda_inference
# (security-groups.tf), reaching DynamoDB/S3 only via the VPC Gateway
# Endpoints in vpc-endpoints.tf. This is a defense-in-depth network
# boundary, not a functional requirement -- the container image pull
# itself is handled by the Lambda service's own infrastructure outside
# your VPC regardless of this function's VPC config, so (unlike Fargate
# pulling from private subnets, see security-groups.tf's ecs_pipeline
# comment) no ECR VPC endpoint is needed here.
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
  # API Gateway's REST API integration timeout is a hard, non-configurable
  # 29s ceiling -- any value here at or above that just gets cut off by API
  # Gateway with a 504 before this timeout would ever fire.
  timeout = 29
  # Lambda CPU scales with memory (roughly linear up to ~1,769MB = 1 vCPU).
  # Bumped from 1024 -- confirmed live via CloudWatch that this Lambda's
  # cold start repeatedly hit Lambda's own non-configurable 10-second
  # INIT PHASE ceiling (INIT_REPORT ... Phase: init Status: timeout,
  # independent of this resource's own `timeout` above) even before
  # scikit-learn/pandas/joblib were added to requirements.txt for the
  # backtesting harness -- AWS's own documented guidance for exactly this
  # failure mode is more memory/CPU during init, not a longer timeout
  # (which can't help anyway, since the init-phase cap isn't governed by
  # it). Runtime memory usage measured well under 512MB even on the
  # heaviest requests seen (season leaderboards) -- this is sized for
  # import/init CPU, not actual memory need.
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
