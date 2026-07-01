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

# ── Deferred to the Lambda pass ───────────────────────────────────────────────
# The following resources require at least one method to exist on the API
# before AWS will accept them. Add them once routes and Lambda integrations
# are defined:
#
#   aws_api_gateway_deployment  -- triggers map should list each integration ID
#   aws_api_gateway_stage       -- references the deployment
#   aws_api_gateway_method_settings -- throttle settings per method
#   aws_api_gateway_usage_plan  -- references the stage
