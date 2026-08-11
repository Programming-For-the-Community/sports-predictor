# Routes for the two NCAAFB serving Lambdas. All resources sit under
# aws_api_gateway_rest_api.main (api-gateway.tf); the shared
# aws_api_gateway_deployment/stage/usage-plan stay owned in
# api-gateway-nfl-predict.tf, whose deployment trigger list includes
# this file's resources too.
#
#   GET /ncaafb/events                                              -> ncaafb_predict_read
#   GET /ncaafb/models                                              -> ncaafb_predict_read
#   GET /ncaafb/season                                              -> ncaafb_predict_read
#   GET /ncaafb/predictions/events/{event_id}                       -> ncaafb_predict_read (cache), async-computed by ncaafb_predict
#   GET /ncaafb/predictions/events/{event_id}/players/{entity_id}   -> ncaafb_predict_read (cache), async-computed by ncaafb_predict

resource "aws_api_gateway_resource" "ncaafb" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "ncaafb"
}

resource "aws_api_gateway_resource" "ncaafb_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "ncaafb_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaafb_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaafb_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaafb_events.id
  http_method             = aws_api_gateway_method.ncaafb_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaafb_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "ncaafb_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "ncaafb_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaafb_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaafb_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaafb_models.id
  http_method             = aws_api_gateway_method.ncaafb_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaafb_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "ncaafb_season" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb.id
  path_part   = "season"
}

resource "aws_api_gateway_method" "ncaafb_season" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaafb_season.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaafb_season" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaafb_season.id
  http_method             = aws_api_gateway_method.ncaafb_season.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaafb_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "ncaafb_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "ncaafb_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "ncaafb_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb_predictions_events.id
  path_part   = "{event_id}"
}

resource "aws_api_gateway_resource" "ncaafb_predictions_event_players" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb_predictions_event.id
  path_part   = "players"
}

resource "aws_api_gateway_resource" "ncaafb_predictions_event_player" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb_predictions_event_players.id
  path_part   = "{entity_id}"
}

# --- GET /ncaafb/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "ncaafb_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaafb_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "ncaafb_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaafb_predictions_event.id
  http_method             = aws_api_gateway_method.ncaafb_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaafb_predict_read.invoke_arn # cache read-through; see handler.py
}

# --- GET /ncaafb/predictions/events/{event_id}/players/{entity_id} -------------

resource "aws_api_gateway_method" "ncaafb_predict_player" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaafb_predictions_event_player.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id"    = true
    "method.request.path.entity_id"   = true
    "method.request.querystring.stat" = true
  }
}

resource "aws_api_gateway_integration" "ncaafb_predict_player" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaafb_predictions_event_player.id
  http_method             = aws_api_gateway_method.ncaafb_predict_player.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaafb_predict_read.invoke_arn
}

# --- CORS preflight (OPTIONS) -------------------------------------------
# Own local map (not api-gateway-nfl-predict.tf's local.cors_resources --
# Terraform can't redeclare the same local name twice in one root module,
# and this file deliberately keeps every NCAAFB-owned resource out of
# that NFL-named file, touching it only for the one unavoidable shared-
# deployment-trigger addition described above). Same MOCK-integration
# pattern/reasoning as api-gateway-nfl-predict.tf's own CORS block.
locals {
  ncaafb_cors_resources = {
    events         = aws_api_gateway_resource.ncaafb_events.id
    models         = aws_api_gateway_resource.ncaafb_models.id
    season         = aws_api_gateway_resource.ncaafb_season.id
    predict_event  = aws_api_gateway_resource.ncaafb_predictions_event.id
    predict_player = aws_api_gateway_resource.ncaafb_predictions_event_player.id
  }
}

resource "aws_api_gateway_method" "ncaafb_cors" {
  for_each      = local.ncaafb_cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "ncaafb_cors" {
  for_each    = local.ncaafb_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.ncaafb_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "ncaafb_cors" {
  for_each    = local.ncaafb_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.ncaafb_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "ncaafb_cors" {
  for_each    = local.ncaafb_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.ncaafb_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.ncaafb_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
