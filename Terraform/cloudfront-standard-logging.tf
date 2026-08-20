# CloudFront standard logging (v2), delivered to a CloudWatch Logs log
# group; feeds the geo-blocked-traffic side of
# cloudwatch-dashboard-viewer-analytics.tf. Uses log delivery rather than
# real-time/Kinesis logging to avoid per-shard cost. City is not available
# in this pipeline -- only c-country -- since standard logging v2 has no
# CloudFront-level field for it.
#
# Must run in us-east-1 regardless of var.region: the CloudWatch Logs
# Delivery API and the destination log group are both required to live
# there.

resource "aws_cloudwatch_log_group" "cloudfront_edge_access_logs" {
  provider = aws.us_east_1

  name              = "/aws/vendedlogs/${var.project}-cloudfront-edge-access"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
  })
}

# us-east-1 twin of iam-stepfunctions-orchestrator.tf's vended_logs policy;
# CloudWatch Logs resource policies are per-region. Capped at 10 per
# account per region.
resource "aws_cloudwatch_log_resource_policy" "vended_logs_us_east_1" {
  provider = aws.us_east_1

  policy_name = "${var.project}-vended-logs-us-east-1"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource  = "arn:aws:logs:us-east-1:${var.account_id}:log-group:/aws/vendedlogs/*:*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_delivery_source" "cloudfront_edge_access_logs" {
  provider = aws.us_east_1

  name         = "${var.project}-cloudfront-edge-access"
  log_type     = "ACCESS_LOGS"
  resource_arn = aws_cloudfront_distribution.main.arn
}

resource "aws_cloudwatch_log_delivery_destination" "cloudfront_edge_access_logs" {
  provider = aws.us_east_1

  name          = "${var.project}-cloudfront-edge-access"
  output_format = "json"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.cloudfront_edge_access_logs.arn
  }
}

resource "aws_cloudwatch_log_delivery" "cloudfront_edge_access_logs" {
  provider = aws.us_east_1

  # Explicit since no argument here references vended_logs_us_east_1;
  # without it Terraform has no ordering guarantee the policy exists
  # before CreateDelivery runs.
  depends_on = [aws_cloudwatch_log_resource_policy.vended_logs_us_east_1]

  delivery_source_name     = aws_cloudwatch_log_delivery_source.cloudfront_edge_access_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.cloudfront_edge_access_logs.arn

  record_fields = [
    "date", "time", "c-ip", "c-country", "cs-method", "cs-uri-stem",
    "cs-uri-query", "sc-status", "x-edge-result-type",
    "x-edge-detailed-result-type", "cs(User-Agent)",
  ]
}
