# GET /nfl/live-scores -> nfl_live_scores (lambda-nfl-live-scores.tf).
resource "aws_api_gateway_resource" "nfl_live_scores" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.nfl.id
  path_part   = "live-scores"
}

resource "aws_api_gateway_method" "nfl_live_scores" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.nfl_live_scores.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "nfl_live_scores" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.nfl_live_scores.id
  http_method             = aws_api_gateway_method.nfl_live_scores.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.nfl_live_scores.invoke_arn
}
