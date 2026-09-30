# One JSON access-log line per API request, feeding the Platform
# dashboard's "Security & access" widgets. Behind CloudFront the source
# IP here is always an edge location's -- the real client IP is in the
# CloudFront edge access log (cloudfront-standard-logging.tf) -- so each
# line identifies its caller by Cognito user instead.

# Account-and-region-wide setting (one per account): API Gateway needs it
# to write any stage's logs to CloudWatch. It was unset in this account.
resource "aws_iam_role" "api_gateway_cloudwatch" {
  name = "${var.project}-api-gateway-cloudwatch"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "apigateway.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

resource "aws_iam_role_policy_attachment" "api_gateway_cloudwatch" {
  role       = aws_iam_role.api_gateway_cloudwatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "main" {
  cloudwatch_role_arn = aws_iam_role.api_gateway_cloudwatch.arn

  depends_on = [aws_iam_role_policy_attachment.api_gateway_cloudwatch]
}

resource "aws_cloudwatch_log_group" "api_gateway_access" {
  name              = "/aws/apigateway/${var.project}-access"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

locals {
  # responseLatency is written unquoted so Logs Insights reads it as a
  # number (the Platform dashboard's latency percentiles). Every other
  # value stays a string; integrationLatency can be "-".
  api_gateway_access_log_format = replace(jsonencode({
    requestId          = "$context.requestId"
    requestTime        = "$context.requestTimeEpoch"
    httpMethod         = "$context.httpMethod"
    resourcePath       = "$context.resourcePath"
    status             = "$context.status"
    responseLatency    = "$context.responseLatency"
    integrationLatency = "$context.integrationLatency"
    userSub            = "$context.authorizer.claims.sub"
    wafResponseCode    = "$context.wafResponseCode"
    errorMessage       = "$context.error.message"
  }), "\"$context.responseLatency\"", "$context.responseLatency")
}
