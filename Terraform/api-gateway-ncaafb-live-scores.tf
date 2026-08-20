# GET /ncaafb/live-scores -> ncaafb_live_scores (lambda-ncaafb-live-scores.tf),
# in its own file since it targets a separate Lambda. CORS is added to
# api-gateway-ncaafb-predict.tf's local.ncaafb_cors_resources map, and this
# resource/method/integration are added to api-gateway-nfl-predict.tf's
# shared aws_api_gateway_deployment trigger list.
resource "aws_api_gateway_resource" "ncaafb_live_scores" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.ncaafb.id
  path_part   = "live-scores"
}

resource "aws_api_gateway_method" "ncaafb_live_scores" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.ncaafb_live_scores.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "ncaafb_live_scores" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.ncaafb_live_scores.id
  http_method             = aws_api_gateway_method.ncaafb_live_scores.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.ncaafb_live_scores.invoke_arn
}
