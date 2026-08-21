# Routes for the two NCAA MBB serving Lambdas under aws_api_gateway_rest_api.main.
# Deployment/stage/usage-plan are managed in api-gateway-nfl-predict.tf.
#
#   GET /ncaambb/events                                              -> ncaambb_predict_read
#   GET /ncaambb/models                                              -> ncaambb_predict_read
#   GET /ncaambb/season                                              -> ncaambb_predict_read
#   GET /ncaambb/predictions/events/{event_id}                       -> ncaambb_predict_read (cache), async-computed by ncaambb_predict
#   GET /ncaambb/predictions/events/{event_id}/players/{entity_id}   -> ncaambb_predict_read (cache), async-computed by ncaambb_predict
#
# No live-scores resource yet -- that's step 7, not built here. The CORS
# locals map below grows a live_scores entry then, same as
# api-gateway-nba-predict.tf's own.

resource "aws_api_gateway_resource" "ncaambb" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "ncaambb"
}

resource "aws_api_gateway_resource" "ncaambb_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "ncaambb_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaambb_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaambb_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaambb_events.id
  http_method             = aws_api_gateway_method.ncaambb_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaambb_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "ncaambb_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "ncaambb_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaambb_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaambb_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaambb_models.id
  http_method             = aws_api_gateway_method.ncaambb_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaambb_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "ncaambb_season" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb.id
  path_part   = "season"
}

resource "aws_api_gateway_method" "ncaambb_season" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaambb_season.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaambb_season" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaambb_season.id
  http_method             = aws_api_gateway_method.ncaambb_season.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaambb_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "ncaambb_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "ncaambb_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "ncaambb_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb_predictions_events.id
  path_part   = "{event_id}"
}

resource "aws_api_gateway_resource" "ncaambb_predictions_event_players" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb_predictions_event.id
  path_part   = "players"
}

resource "aws_api_gateway_resource" "ncaambb_predictions_event_player" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaambb_predictions_event_players.id
  path_part   = "{entity_id}"
}

# --- GET /ncaambb/predictions/events/{event_id} ------------------------------

resource "aws_api_gateway_method" "ncaambb_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaambb_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "ncaambb_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaambb_predictions_event.id
  http_method             = aws_api_gateway_method.ncaambb_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaambb_predict_read.invoke_arn # cache read-through
}

# --- GET /ncaambb/predictions/events/{event_id}/players/{entity_id} ----------

resource "aws_api_gateway_method" "ncaambb_predict_player" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaambb_predictions_event_player.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id"    = true
    "method.request.path.entity_id"   = true
    "method.request.querystring.stat" = true
  }
}

resource "aws_api_gateway_integration" "ncaambb_predict_player" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaambb_predictions_event_player.id
  http_method             = aws_api_gateway_method.ncaambb_predict_player.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaambb_predict_read.invoke_arn
}

# --- CORS preflight (OPTIONS) -------------------------------------------
locals {
  ncaambb_cors_resources = {
    events         = aws_api_gateway_resource.ncaambb_events.id
    models         = aws_api_gateway_resource.ncaambb_models.id
    season         = aws_api_gateway_resource.ncaambb_season.id
    predict_event  = aws_api_gateway_resource.ncaambb_predictions_event.id
    predict_player = aws_api_gateway_resource.ncaambb_predictions_event_player.id
    # live-scores resource joins here in step 7, once it exists.
  }
}

resource "aws_api_gateway_method" "ncaambb_cors" {
  for_each      = local.ncaambb_cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "ncaambb_cors" {
  for_each    = local.ncaambb_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.ncaambb_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "ncaambb_cors" {
  for_each    = local.ncaambb_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.ncaambb_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "ncaambb_cors" {
  for_each    = local.ncaambb_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.ncaambb_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.ncaambb_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
