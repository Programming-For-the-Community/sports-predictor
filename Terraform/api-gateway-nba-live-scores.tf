# GET /nba/live-scores -> nba_live_scores (lambda-nba-live-scores.tf).
# Split out of api-gateway-nba-predict.tf since it targets a third,
# separate Lambda -- same split api-gateway-ncaafb-live-scores.tf makes
# from api-gateway-ncaafb-predict.tf. CORS for this route is added to
# api-gateway-nba-predict.tf's own local.nba_cors_resources map, and this
# resource/method/integration are added to api-gateway-nfl-predict.tf's
# shared aws_api_gateway_deployment trigger list (one REST API, one
# deployment -- see that file's own comment).
resource "aws_api_gateway_resource" "nba_live_scores" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nba.id
  path_part   = "live-scores"
}

resource "aws_api_gateway_method" "nba_live_scores" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nba_live_scores.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nba_live_scores" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nba_live_scores.id
  http_method             = aws_api_gateway_method.nba_live_scores.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nba_live_scores.invoke_arn
}
