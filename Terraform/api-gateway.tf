# REST API with a Cognito User Pool authorizer and usage-plan throttling.
resource "aws_api_gateway_rest_api" "main" {
  name = "${var.project}-api"

  # The default execute-api.<region>.amazonaws.com endpoint can't be
  # restricted below TLS 1.0 and has no security_policy control at all --
  # disabled once api-gateway-domain.tf's custom domain (TLS_1_2) takes
  # over as CloudFront's origin, so the weak default endpoint doesn't stay
  # reachable in parallel for no reason.
  disable_execute_api_endpoint = true

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

# Deployment/stage/usage plan live in api-gateway-nfl-predict.tf since AWS
# rejects those resources on an API with zero methods.

# Overrides API Gateway's default 403 "Missing Authentication Token" for
# undefined paths/methods to a real 404, account/API-wide.
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

# Genericizes the BODY of every other 4xx API Gateway generates itself --
# covers Cognito UNAUTHORIZED (401) and usage-plan THROTTLED (429), both
# left on this catch-all. Status code is not overridden: api_client.dart
# reactively retries on a real 401 (forced token refresh) and 429
# (backoff), so those two codes keep their meaning. Only the AWS-default
# message text is stripped.
resource "aws_api_gateway_gateway_response" "default_4xx" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "DEFAULT_4XX"

  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }

  response_templates = {
    "application/json" = jsonencode({ error = "Not found" })
  }
}

# Nothing in the front end keys off ACCESS_DENIED (unlike UNAUTHORIZED/
# THROTTLED above), so it disappears into the same "route doesn't exist"
# shape missing_auth_token returns.
resource "aws_api_gateway_gateway_response" "access_denied" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "ACCESS_DENIED"
  status_code   = "404"

  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }

  response_templates = {
    "application/json" = jsonencode({ error = "Not found" })
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
