# CloudFront standard logging (v2), delivered straight to a CloudWatch
# Logs log group -- the source for the "turned away" section of
# cloudwatch-dashboard-viewer-analytics.tf. Deliberately NOT real-time
# logs (which require a Kinesis Data Stream, an ongoing per-shard cost)
# -- this project's only non-Lambda source of country-level geo data
# (`c-country`) is a request CloudFront blocks via geo-restriction, which
# never reaches any Lambda at all (the whole reason this pipeline exists
# -- library/serving/viewer_analytics.py's own docstring covers ALLOWED
# traffic, sourced from headers CloudFront only adds when forwarding to
# an origin). Standard logging (v2) can select `c-country` too (confirmed
# against AWS's own standard-logging.html docs, which cross-list it under
# "you can also select a subset of real-time access log fields") and
# costs nothing beyond ordinary CloudWatch Logs vended-log ingestion --
# no Kinesis, no Firehose, no S3 lifecycle to manage.
#
# City is NOT available here, unlike the allowed-traffic pipeline --
# there is no CloudFront-level field for it at all (confirmed against the
# same docs: only c-country is listed among the real-time fields standard
# logging v2 can also select). City only ever comes from the
# CloudFront-Viewer-City header CloudFront adds when forwarding a request
# to an origin -- which by construction never happens for a geo-blocked
# request. This is an architectural ceiling, not a cost tradeoff.
#
# Must run in us-east-1 regardless of var.region -- AWS's own docs for
# this feature say every one of these CloudWatch Logs Delivery API calls
# must be made against us-east-1. That doc also shows a working S3 cross-
# region example, which the destination log group itself was originally
# read to permit here too -- confirmed WRONG by a real `terraform apply`:
# PutDeliveryDestination 400'd with "Region from identity does not match
# the Destination Resource ARN" when the log group lived in var.region
# (us-east-2) while the API call ran in us-east-1. Unlike S3, a CloudWatch
# Logs destination has to live in us-east-1 itself, so the log group is
# pinned there too now (same requirement ACM already has for a
# CloudFront-attached certificate, see acm.tf).
#
# Log group name lives under /aws/vendedlogs/, same prefix convention as
# iam-stepfunctions-orchestrator.tf's aws_cloudwatch_log_resource_policy.
# vended_logs -- but that policy's own Resource ARN is hardcoded to
# var.region (us-east-2) and CloudWatch Logs resource policies are
# per-region, so it can't cover a us-east-1 log group. A second, us-east-1
# copy of that same policy is defined below instead of trying to reuse it.

resource "aws_cloudwatch_log_group" "cloudfront_edge_access_logs" {
  provider = aws.us_east_1

  name              = "/aws/vendedlogs/${var.project}-cloudfront-edge-access"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
  })
}

# us-east-1-scoped twin of iam-stepfunctions-orchestrator.tf's
# vended_logs policy -- CloudWatch Logs resource policies are per-region,
# so the existing one (created against var.region) has no effect here.
# Capped at 10 per account per region same as that file's own comment
# notes; us-east-1 had none before this, so headroom isn't a concern.
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

  # Explicit, since nothing else in this resource's own arguments
  # references vended_logs_us_east_1 -- without it, Terraform has no
  # reason to create the resource policy before the delivery, and
  # CreateDelivery would fail the same way PutDeliveryDestination did
  # before the region fix above, just for a permissions reason instead of
  # a region one.
  depends_on = [aws_cloudwatch_log_resource_policy.vended_logs_us_east_1]

  delivery_source_name     = aws_cloudwatch_log_delivery_source.cloudfront_edge_access_logs.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.cloudfront_edge_access_logs.arn

  # Order doesn't matter for Logs Insights (it queries the delivered JSON
  # object's keys directly, not positionally) -- kept alphabetical-ish by
  # topic only for readability here.
  record_fields = [
    "date", "time", "c-ip", "c-country", "cs-method", "cs-uri-stem",
    "cs-uri-query", "sc-status", "x-edge-result-type",
    "x-edge-detailed-result-type", "cs(User-Agent)",
  ]
}
