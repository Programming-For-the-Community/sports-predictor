# Routes for the NFL inference Lambda (lambda-nfl-predict.tf). Both
# resources sit under aws_api_gateway_rest_api.main (api-gateway.tf) --
# see Source/aws-lambdas/nfl/predict/handler.py's docstring for the exact
# request/response contract each route serves.
#
#   GET /nfl/predictions/events/{event_id}
#   GET /nfl/predictions/events/{event_id}/players/{entity_id}
#
# This is also where the deployment/stage/usage plan api-gateway.tf
# deferred live -- AWS won't accept those until at least one method
# exists, and this is the first (and so far only) route added.

resource "aws_api_gateway_resource" "nfl" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "nfl"
}

resource "aws_api_gateway_resource" "nfl_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "nfl_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nfl_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_events.id
  http_method             = aws_api_gateway_method.nfl_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict.invoke_arn
}

resource "aws_api_gateway_resource" "nfl_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "nfl_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nfl_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_models.id
  http_method             = aws_api_gateway_method.nfl_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict.invoke_arn
}

resource "aws_api_gateway_resource" "nfl_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "nfl_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "nfl_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions_events.id
  path_part   = "{event_id}"
}

resource "aws_api_gateway_resource" "nfl_predictions_event_players" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions_event.id
  path_part   = "players"
}

resource "aws_api_gateway_resource" "nfl_predictions_event_player" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl_predictions_event_players.id
  path_part   = "{entity_id}"
}

# --- GET /nfl/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "nfl_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "nfl_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_predictions_event.id
  http_method             = aws_api_gateway_method.nfl_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict.invoke_arn
}

# --- GET /nfl/predictions/events/{event_id}/players/{entity_id} -------------

resource "aws_api_gateway_method" "nfl_predict_player" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_predictions_event_player.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id"    = true
    "method.request.path.entity_id"   = true
    "method.request.querystring.stat" = true
  }
}

resource "aws_api_gateway_integration" "nfl_predict_player" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_predictions_event_player.id
  http_method             = aws_api_gateway_method.nfl_predict_player.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_predict.invoke_arn
}

# --- Deployment / stage / usage plan -----------------------------------------

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.nfl.id,
      aws_api_gateway_resource.nfl_predictions.id,
      aws_api_gateway_resource.nfl_predictions_events.id,
      aws_api_gateway_resource.nfl_predictions_event.id,
      aws_api_gateway_resource.nfl_predictions_event_players.id,
      aws_api_gateway_resource.nfl_predictions_event_player.id,
      aws_api_gateway_method.nfl_predict_event.id,
      aws_api_gateway_integration.nfl_predict_event.id,
      aws_api_gateway_method.nfl_predict_player.id,
      aws_api_gateway_integration.nfl_predict_player.id,
      aws_api_gateway_gateway_response.missing_auth_token.id,
      aws_api_gateway_resource.nfl_events.id,
      aws_api_gateway_method.nfl_events.id,
      aws_api_gateway_integration.nfl_events.id,
      aws_api_gateway_resource.nfl_models.id,
      aws_api_gateway_method.nfl_models.id,
      aws_api_gateway_integration.nfl_models.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "main" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  deployment_id = aws_api_gateway_deployment.main.id
  stage_name    = var.environment

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Throttling only -- no API key required yet (Cognito already authenticates
# every caller). A usage plan is still the right place for throttling even
# without keys: it's the mechanism API Gateway exposes for it.
resource "aws_api_gateway_method_settings" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_api_gateway_usage_plan" "main" {
  name = "${var.project}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.main.id
    stage  = aws_api_gateway_stage.main.stage_name
  }

  throttle_settings {
    burst_limit = 20
    rate_limit  = 10
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}
