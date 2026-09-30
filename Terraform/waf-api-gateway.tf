# API Gateway's custom domain is publicly resolvable, so requests could
# reach it directly and skip CloudFront's own WAF and geo restriction.
# CloudFront sends a secret header on every request to the API origin
# (cloudfront.tf); this ACL allows only requests carrying it.

resource "random_password" "cloudfront_origin_secret" {
  length  = 48
  special = false
}

locals {
  cloudfront_origin_secret_header = "x-origin-verify"
}

resource "aws_wafv2_web_acl" "api_gateway" {
  name        = "${var.project}-api-gateway"
  description = "Allows only requests forwarded by the CloudFront distribution."
  scope       = "REGIONAL"

  default_action {
    block {
      custom_response {
        response_code            = 404
        custom_response_body_key = "not-found"
      }
    }
  }

  rule {
    name     = "allow-cloudfront-origin"
    priority = 0

    action {
      allow {}
    }

    statement {
      byte_match_statement {
        search_string         = random_password.cloudfront_origin_secret.result
        positional_constraint = "EXACTLY"

        field_to_match {
          single_header {
            name = local.cloudfront_origin_secret_header
          }
        }

        text_transformation {
          priority = 0
          type     = "NONE"
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project}-api-allow-cloudfront-origin"
      sampled_requests_enabled   = true
    }
  }

  # Same body API Gateway itself returns for an unknown or unauthorized
  # route (api-gateway.tf's gateway responses).
  custom_response_body {
    key          = "not-found"
    content      = jsonencode({ error = "Not found" })
    content_type = "APPLICATION_JSON"
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-api-gateway-waf"
    sampled_requests_enabled   = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

resource "aws_wafv2_web_acl_association" "api_gateway" {
  resource_arn = aws_api_gateway_stage.main.arn
  web_acl_arn  = aws_wafv2_web_acl.api_gateway.arn

  # Associate only once every CloudFront edge is sending the secret header
  # (the distribution waits for deployment by default); associating first
  # would block CloudFront's own API requests during propagation.
  depends_on = [aws_cloudfront_distribution.main]
}

# Log group name MUST start with "aws-waf-logs-".
resource "aws_cloudwatch_log_group" "waf_api_gateway" {
  name              = "aws-waf-logs-${var.project}-api-gateway"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Logs only blocked requests -- every allowed request already appears in
# the API access log (api-gateway-access-logs.tf).
resource "aws_wafv2_web_acl_logging_configuration" "api_gateway" {
  resource_arn            = aws_wafv2_web_acl.api_gateway.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf_api_gateway.arn]

  # The origin secret must never be written to the logs.
  redacted_fields {
    single_header {
      name = local.cloudfront_origin_secret_header
    }
  }

  logging_filter {
    default_behavior = "DROP"

    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"

      condition {
        action_condition {
          action = "BLOCK"
        }
      }
    }
  }
}
