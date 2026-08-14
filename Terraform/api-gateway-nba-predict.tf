# Routes for the two NBA serving Lambdas. All resources sit under
# aws_api_gateway_rest_api.main (api-gateway.tf); the shared
# aws_api_gateway_deployment/stage/usage-plan stay owned in
# api-gateway-nfl-predict.tf, whose deployment trigger list includes
# this file's resources too.
#
#   GET /nba/events                                              -> nba_predict_read
#   GET /nba/models                                              -> nba_predict_read
#   GET /nba/season                                              -> nba_predict_read
#   GET /nba/predictions/events/{event_id}                       -> nba_predict_read (cache), async-computed by nba_predict
#   GET /nba/predictions/events/{event_id}/players/{entity_id}   -> nba_predict_read (cache), async-computed by nba_predict

resource "aws_api_gateway_resource" "nba" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "nba"
}

resource "aws_api_gateway_resource" "nba_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "nba_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nba_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nba_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nba_events.id
  http_method             = aws_api_gateway_method.nba_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nba_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nba_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "nba_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nba_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nba_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nba_models.id
  http_method             = aws_api_gateway_method.nba_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nba_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nba_season" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba.id
  path_part   = "season"
}

resource "aws_api_gateway_method" "nba_season" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nba_season.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nba_season" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nba_season.id
  http_method             = aws_api_gateway_method.nba_season.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nba_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "nba_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "nba_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "nba_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba_predictions_events.id
  path_part   = "{event_id}"
}

resource "aws_api_gateway_resource" "nba_predictions_event_players" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba_predictions_event.id
  path_part   = "players"
}

resource "aws_api_gateway_resource" "nba_predictions_event_player" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba_predictions_event_players.id
  path_part   = "{entity_id}"
}

# --- GET /nba/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "nba_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nba_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "nba_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nba_predictions_event.id
  http_method             = aws_api_gateway_method.nba_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nba_predict_read.invoke_arn # cache read-through; see handler.py
}

# --- GET /nba/predictions/events/{event_id}/players/{entity_id} -------------

resource "aws_api_gateway_method" "nba_predict_player" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nba_predictions_event_player.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id"    = true
    "method.request.path.entity_id"   = true
    "method.request.querystring.stat" = true
  }
}

resource "aws_api_gateway_integration" "nba_predict_player" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nba_predictions_event_player.id
  http_method             = aws_api_gateway_method.nba_predict_player.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nba_predict_read.invoke_arn
}

# --- CORS preflight (OPTIONS) -------------------------------------------
# Own local map (not api-gateway-nfl-predict.tf's local.cors_resources --
# Terraform can't redeclare the same local name twice in one root module,
# and this file deliberately keeps every NBA-owned resource out of
# that NFL-named file, touching it only for the one unavoidable shared-
# deployment-trigger addition described above). Same MOCK-integration
# pattern/reasoning as api-gateway-nfl-predict.tf's own CORS block.
locals {
  nba_cors_resources = {
    events         = aws_api_gateway_resource.nba_events.id
    models         = aws_api_gateway_resource.nba_models.id
    season         = aws_api_gateway_resource.nba_season.id
    predict_event  = aws_api_gateway_resource.nba_predictions_event.id
    predict_player = aws_api_gateway_resource.nba_predictions_event_player.id
    # live-scores -- resource declared in api-gateway-nba-live-scores.tf,
    # a third Lambda target.
    live_scores = aws_api_gateway_resource.nba_live_scores.id
  }
}

resource "aws_api_gateway_method" "nba_cors" {
  for_each      = local.nba_cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "nba_cors" {
  for_each    = local.nba_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.nba_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "nba_cors" {
  for_each    = local.nba_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.nba_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "nba_cors" {
  for_each    = local.nba_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.nba_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.nba_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
