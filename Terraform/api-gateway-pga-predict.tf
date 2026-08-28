# Routes for the two PGA serving Lambdas under aws_api_gateway_rest_api.main.
# Deployment/stage/usage-plan are managed in api-gateway-nfl-predict.tf --
# see that file's own "PGA routes" block appended to its redeployment-
# trigger sha1 list.
#
#   GET /pga/events                          -> pga_predict_read
#   GET /pga/models                          -> pga_predict_read
#   GET /pga/predictions/events/{event_id}   -> pga_predict_read (cache), async-computed by pga_predict
#   GET /pga/live-scores                     -> pga_live_scores (api-gateway-pga-live-scores.tf, separate Lambda)
#
# No GET /pga/season (no season-long standings/odds concept) and no
# per-golfer prediction sub-route (unlike NBA's .../players/{entity_id})
# -- one field compute already scores every golfer, there's nothing
# narrower to fetch. See aws-lambdas/pga/predict-read/handler.py's own
# docstring.

resource "aws_api_gateway_resource" "pga" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "pga"
}

resource "aws_api_gateway_resource" "pga_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.pga.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "pga_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.pga_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "pga_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.pga_events.id
  http_method             = aws_api_gateway_method.pga_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.pga_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "pga_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.pga.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "pga_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.pga_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "pga_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.pga_models.id
  http_method             = aws_api_gateway_method.pga_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.pga_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "pga_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.pga.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "pga_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.pga_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "pga_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.pga_predictions_events.id
  path_part   = "{event_id}"
}

# --- GET /pga/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "pga_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.pga_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "pga_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.pga_predictions_event.id
  http_method             = aws_api_gateway_method.pga_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.pga_predict_read.invoke_arn # cache read-through
}

# --- CORS preflight (OPTIONS) -------------------------------------------
locals {
  pga_cors_resources = {
    events        = aws_api_gateway_resource.pga_events.id
    models        = aws_api_gateway_resource.pga_models.id
    predict_event = aws_api_gateway_resource.pga_predictions_event.id
    live_scores   = aws_api_gateway_resource.pga_live_scores.id
  }
}

resource "aws_api_gateway_method" "pga_cors" {
  for_each      = local.pga_cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "pga_cors" {
  for_each    = local.pga_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.pga_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "pga_cors" {
  for_each    = local.pga_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.pga_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "pga_cors" {
  for_each    = local.pga_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.pga_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.pga_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
