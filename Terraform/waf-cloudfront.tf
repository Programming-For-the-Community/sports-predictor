# WAF WebACL attached to the CloudFront distribution (cloudfront.tf),
# blocking known-malicious IPs via AWS's own curated reputation list --
# nothing in front of CloudFront blocked IP-reputation traffic before
# this; geo-restriction (cloudfront.tf's own restrictions block) only
# ever filtered by country.
#
# Must run in us-east-1 regardless of var.region: a WebACL with
# scope = "CLOUDFRONT" is only ever created there, same reason
# cloudfront-standard-logging.tf uses the us_east_1 provider alias.
resource "aws_wafv2_web_acl" "cloudfront" {
  provider = aws.us_east_1

  name        = "${var.project}-cloudfront"
  description = "Blocks known-malicious IPs in front of the CloudFront distribution."
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # AWS's own curated, continuously-updated malicious/reputation IP list --
  # exactly "known malicious IPs", no IP data to source or maintain here.
  rule {
    name     = "amazon-ip-reputation-list"
    priority = 0

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project}-amazon-ip-reputation-list"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-cloudfront-waf"
    sampled_requests_enabled   = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
  })
}

# Log group name MUST start with "aws-waf-logs-" -- AWS rejects any other
# prefix for a WAF logging destination.
resource "aws_cloudwatch_log_group" "waf_cloudfront" {
  provider = aws.us_east_1

  name              = "aws-waf-logs-${var.project}-cloudfront"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
  })
}

resource "aws_wafv2_web_acl_logging_configuration" "cloudfront" {
  provider = aws.us_east_1

  resource_arn            = aws_wafv2_web_acl.cloudfront.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf_cloudfront.arn]
}
