# Routes for the two F1 serving Lambdas under aws_api_gateway_rest_api.main.
# Deployment/stage/usage-plan are managed in api-gateway-nfl-predict.tf --
# see that file's own "F1 routes" block appended to its redeployment-
# trigger sha1 list.
#
#   GET /f1/events                          -> f1_predict_read
#   GET /f1/models                          -> f1_predict_read
#   GET /f1/season                          -> f1_predict_read (cache), async-computed weekly by f1_predict
#   GET /f1/predictions/events/{event_id}   -> f1_predict_read (cache), async-computed by f1_predict
#
# No live-scores route (unlike PGA's own api-gateway-pga-live-scores.tf) --
# F1's own live-scores Lambda is still deferred (project-f1-onboarding
# memory). No per-driver prediction sub-route (unlike NBA's
# .../players/{entity_id}) -- one race compute already scores every
# driver (and every constructor, for a "field" event), there's nothing
# narrower to fetch. See aws-lambdas/f1/predict-read/handler.py's own
# docstring.

resource "aws_api_gateway_resource" "f1" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "f1"
}

resource "aws_api_gateway_resource" "f1_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "f1_events" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.f1_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "f1_events" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.f1_events.id
  http_method             = aws_api_gateway_method.f1_events.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.f1_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "f1_models" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1.id
  path_part   = "models"
}

resource "aws_api_gateway_method" "f1_models" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.f1_models.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "f1_models" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.f1_models.id
  http_method             = aws_api_gateway_method.f1_models.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.f1_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "f1_season" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1.id
  path_part   = "season"
}

resource "aws_api_gateway_method" "f1_season" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.f1_season.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "f1_season" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.f1_season.id
  http_method             = aws_api_gateway_method.f1_season.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.f1_predict_read.invoke_arn
}

resource "aws_api_gateway_resource" "f1_predictions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1.id
  path_part   = "predictions"
}

resource "aws_api_gateway_resource" "f1_predictions_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1_predictions.id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "f1_predictions_event" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1_predictions_events.id
  path_part   = "{event_id}"
}

# --- GET /f1/predictions/events/{event_id} ---------------------------------

resource "aws_api_gateway_method" "f1_predict_event" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.f1_predictions_event.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id

  request_parameters = {
    "method.request.path.event_id" = true
  }
}

resource "aws_api_gateway_integration" "f1_predict_event" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.f1_predictions_event.id
  http_method             = aws_api_gateway_method.f1_predict_event.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.f1_predict_read.invoke_arn # cache read-through
}

# --- CORS preflight (OPTIONS) -------------------------------------------
locals {
  f1_cors_resources = {
    events        = aws_api_gateway_resource.f1_events.id
    models        = aws_api_gateway_resource.f1_models.id
    season        = aws_api_gateway_resource.f1_season.id
    predict_event = aws_api_gateway_resource.f1_predictions_event.id
  }
}

resource "aws_api_gateway_method" "f1_cors" {
  for_each      = local.f1_cors_resources
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = each.value
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "f1_cors" {
  for_each    = local.f1_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.f1_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "f1_cors" {
  for_each    = local.f1_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.f1_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "f1_cors" {
  for_each    = local.f1_cors_resources
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = each.value
  http_method = aws_api_gateway_method.f1_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.f1_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
