# Routes for the two NHL serving Lambdas under aws_api_gateway_rest_api.main.
# Deployment/stage/usage-plan are managed in api-gateway-nfl-predict.tf.
#
#   GET /nhl/events                                              -> predict_read (shared)
#   GET /nhl/models                                              -> predict_read (shared)
#   GET /nhl/season                                              -> predict_read (shared)
#   GET /nhl/predictions/events/{event_id}                       -> predict_read (shared) (cache), async-computed by nhl_predict
#   GET /nhl/predictions/events/{event_id}/players/{entity_id}   -> predict_read (shared) (cache), async-computed by nhl_predict
#   GET /nhl/live-scores                                         -> nhl_live_scores (api-gateway-nhl-live-scores.tf)
#   GET /nhl/model-performance                                   -> predict_read (shared) (api-gateway-model-performance.tf)

resource "aws_api_gateway_resource" "nhl" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "nhl"
}

resource "aws_api_gateway_resource" "nhl_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "nhl_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nhl_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nhl_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nhl_events.id
  http_method             = aws_api_gateway_method.nhl_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nhl_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "nhl_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nhl_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nhl_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nhl_models.id
  http_method             = aws_api_gateway_method.nhl_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nhl_season" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl.id
  path_part   = "season"
}

resource "aws_api_gateway_method" "nhl_season" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nhl_season.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nhl_season" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nhl_season.id
  http_method             = aws_api_gateway_method.nhl_season.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nhl_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "nhl_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "nhl_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl_predictions_events.id
  path_part   = "{event_id}"
}

resource "aws_api_gateway_resource" "nhl_predictions_event_players" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl_predictions_event.id
  path_part   = "players"
}

resource "aws_api_gateway_resource" "nhl_predictions_event_player" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nhl_predictions_event_players.id
  path_part   = "{entity_id}"
}

# --- GET /nhl/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "nhl_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nhl_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "nhl_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nhl_predictions_event.id
  http_method             = aws_api_gateway_method.nhl_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.predict_read.invoke_arn # cache read-through
}

# --- GET /nhl/predictions/events/{event_id}/players/{entity_id} -------------

resource "aws_api_gateway_method" "nhl_predict_player" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nhl_predictions_event_player.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id"    = true
    "method.request.path.entity_id"   = true
    "method.request.querystring.stat" = true
  }
}

resource "aws_api_gateway_integration" "nhl_predict_player" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nhl_predictions_event_player.id
  http_method             = aws_api_gateway_method.nhl_predict_player.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.predict_read.invoke_arn
}

# --- CORS preflight (OPTIONS) -------------------------------------------
locals {
  nhl_cors_resources = {
    events         = aws_api_gateway_resource.nhl_events.id
    models         = aws_api_gateway_resource.nhl_models.id
    season         = aws_api_gateway_resource.nhl_season.id
    predict_event  = aws_api_gateway_resource.nhl_predictions_event.id
    predict_player = aws_api_gateway_resource.nhl_predictions_event_player.id
    # live-scores resource declared in api-gateway-nhl-live-scores.tf
    live_scores = aws_api_gateway_resource.nhl_live_scores.id
  }
}

resource "aws_api_gateway_method" "nhl_cors" {
  for_each      = local.nhl_cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "nhl_cors" {
  for_each    = local.nhl_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.nhl_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "nhl_cors" {
  for_each    = local.nhl_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.nhl_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "nhl_cors" {
  for_each    = local.nhl_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.nhl_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.nhl_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
