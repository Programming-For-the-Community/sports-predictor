# GET /{sport}/model-performance -> predict_read (shared), for every sport.
# One for_each block rather than six copies: the route is identical for all
# sports (it reads that sport's scorecard file from S3, see
# library.serving.common.get_model_performance). CloudFront already routes
# /{sport}/* to the API (cloudfront.tf's dynamic ordered_cache_behavior), so
# no CloudFront change is needed.
#
# The deployment trigger list in api-gateway-nfl-predict.tf includes these
# resources so API Gateway actually redeploys when they change.
locals {
  model_performance_parents = {
    nfl     = aws_api_gateway_resource.nfl.id
    nba     = aws_api_gateway_resource.nba.id
    ncaafb  = aws_api_gateway_resource.ncaafb.id
    ncaambb = aws_api_gateway_resource.ncaambb.id
    pga     = aws_api_gateway_resource.pga.id
    f1      = aws_api_gateway_resource.f1.id
  }
}

resource "aws_api_gateway_resource" "model_performance" {
  for_each    = local.model_performance_parents
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = each.value
  path_part   = "model-performance"
}

resource "aws_api_gateway_method" "model_performance" {
  for_each      = local.model_performance_parents
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.model_performance[each.key].id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "model_performance" {
  for_each                = local.model_performance_parents
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.model_performance[each.key].id
  http_method             = aws_api_gateway_method.model_performance[each.key].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.predict_read.invoke_arn
}

# --- CORS preflight (OPTIONS) -------------------------------------------

resource "aws_api_gateway_method" "model_performance_cors" {
  for_each      = local.model_performance_parents
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.model_performance[each.key].id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "model_performance_cors" {
  for_each    = local.model_performance_parents
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.model_performance[each.key].id
  http_method = aws_api_gateway_method.model_performance_cors[each.key].http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "model_performance_cors" {
  for_each    = local.model_performance_parents
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.model_performance[each.key].id
  http_method = aws_api_gateway_method.model_performance_cors[each.key].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "model_performance_cors" {
  for_each    = local.model_performance_parents
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.model_performance[each.key].id
  http_method = aws_api_gateway_method.model_performance_cors[each.key].http_method
  status_code = aws_api_gateway_method_response.model_performance_cors[each.key].status_code

  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,OPTIONS'"
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
  }
}
