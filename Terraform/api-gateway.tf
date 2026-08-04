# REST API (v1) -- chosen over HTTP API (v2) because:
#   - Cognito User Pool Authorizer works natively via pool ARN (no issuer URL)
#   - Usage plans and throttling are first-class REST API features
#   - More mature custom domain support for regional endpoints
resource "aws_api_gateway_rest_api" "main" {
  name = "${var.project}-api"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Validates the Cognito JWT on every request before it reaches Lambda.
# The frontend sends the Cognito access token in the Authorization header.
resource "aws_api_gateway_authorizer" "cognito" {
  name            = "${var.project}-cognito"
  rest_api_id     = aws_api_gateway_rest_api.main.id
  type            = "COGNITO_USER_POOLS"
  provider_arns   = [aws_cognito_user_pool.main.arn]
  identity_source = "method.request.header.Authorization"
}

# Deployment/stage/usage plan live in api-gateway-nfl-predict.tf, added
# once the first routes existed (AWS rejects those resources on an API
# with zero methods).

# Without this, a request to an undefined path OR an existing path hit
# with an undefined method both get API Gateway's default
# {"message":"Missing Authentication Token"} 403 -- confusing for a client
# that just made a typo. Overriding this gateway response to a real 404
# covers both cases, account/API-wide, not per-route.
resource "aws_api_gateway_gateway_response" "missing_auth_token" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "MISSING_AUTHENTICATION_TOKEN"
  status_code   = "404"

  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }

  response_templates = {
    "application/json" = jsonencode({ error = "Not found" })
  }
}

# Every OTHER error API Gateway generates itself -- before the request ever
# reaches the Lambda -- also has no CORS headers by default. That includes
# UNAUTHORIZED/ACCESS_DENIED (the Cognito authorizer rejecting a missing or
# expired token) and THROTTLED (the usage plan's rate limit). Confirmed live:
# a request with an invalid token got back a real 401, but with no
# Access-Control-Allow-Origin header at all -- a browser blocks a
# cross-origin response like that from ever reaching application code, so it
# surfaces as a generic "Failed to fetch" instead of a readable 401 the app's
# own ApiClient.get() retry-on-401 logic could react to. DEFAULT_4XX/
# DEFAULT_5XX cover every such API-Gateway-generated error uniformly, not
# just the two known today -- unlike missing_auth_token above, these don't
# override the status code or body, only add the missing header.
resource "aws_api_gateway_gateway_response" "default_4xx" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "DEFAULT_4XX"

  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
}

resource "aws_api_gateway_gateway_response" "default_5xx" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "DEFAULT_5XX"

  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
}
