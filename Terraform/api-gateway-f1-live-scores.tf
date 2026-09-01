# GET /f1/live-scores -> f1_live_scores (lambda-f1-live-scores.tf), in its
# own file since it targets a separate Lambda. CORS is added to
# api-gateway-f1-predict.tf's local.f1_cors_resources map, and this
# resource/method/integration are added to api-gateway-nfl-predict.tf's
# shared aws_api_gateway_deployment trigger list.
resource "aws_api_gateway_resource" "f1_live_scores" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.f1.id
  path_part   = "live-scores"
}

resource "aws_api_gateway_method" "f1_live_scores" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.f1_live_scores.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "f1_live_scores" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.f1_live_scores.id
  http_method             = aws_api_gateway_method.f1_live_scores.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.f1_live_scores.invoke_arn
}
