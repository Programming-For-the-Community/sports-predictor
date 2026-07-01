# REST API (v1) -- chosen over HTTP API (v2) because:
#   - Cognito User Pool Authorizer works natively via pool ARN (no issuer URL)
#   - Usage plans and throttling are first-class REST API features
#   - More mature custom domain support for regional endpoints
#
# Individual routes and Lambda integrations are added in the Lambda pass
# once the inference function exists. The deployment triggers map below
# should be updated at that point to include each integration resource ID so
# Terraform re-deploys the stage when routes change.
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

# Initial deployment of the (currently empty) API. create_before_destroy
# ensures zero-downtime re-deployments when routes are added later.
# Update the redeployment trigger in the Lambda pass by adding each
# aws_api_gateway_integration resource ID to the map.
resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    redeployment = sha1(jsonencode({
      api = aws_api_gateway_rest_api.main.id
      # Lambda pass: add integration IDs here to force re-deploy on route changes
    }))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "main" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  deployment_id = aws_api_gateway_deployment.main.id
  stage_name    = var.environment

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Per-ARCHITECTURE.md: "a low-throughput usage plan... any traffic pattern
# that would hit this limit is almost certainly not you."
resource "aws_api_gateway_method_settings" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

resource "aws_api_gateway_usage_plan" "main" {
  name = "${var.project}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.main.id
    stage  = aws_api_gateway_stage.main.stage_name
  }

  throttle_settings {
    burst_limit = 10
    rate_limit  = 5
  }

  quota_settings {
    limit  = 1000
    period = "DAY"
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}
