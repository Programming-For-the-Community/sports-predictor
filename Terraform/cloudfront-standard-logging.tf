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
# this feature are explicit that the CloudWatch Logs Delivery API these
# 3 resources wrap must be called against us-east-1 even when the
# destination itself lives in another region (same requirement ACM
# already has for a CloudFront-attached certificate, see acm.tf).
#
# Log group name deliberately lives under /aws/vendedlogs/ -- that's the
# exact prefix iam-stepfunctions-orchestrator.tf's aws_cloudwatch_log_
# resource_policy.vended_logs already grants "delivery.logs.amazonaws.com"
# CreateLogStream/PutLogEvents on, account-wide. Reusing that existing
# policy instead of writing a second one -- CloudWatch Logs resource
# policies are account-scoped and capped at 10, so prefix reuse across
# unrelated delivery sources is the intended pattern, not a shortcut.
# UNVERIFIED: that policy was written for the classic vended-logs
# mechanism (VPC Flow Logs/Step Functions); this feature is documented as
# using the same underlying "Logs Delivery" resource-policy model (same
# delivery.logs.amazonaws.com principal, same doc section AWS's CloudFront
# guide links to), but this hasn't been confirmed against a real `terraform
# apply` -- check the CloudFront console's Logging tab shows "Enabled"
# after applying, per AWS's own documented verification step.

resource "aws_cloudwatch_log_group" "cloudfront_edge_access_logs" {
  name              = "/aws/vendedlogs/${var.project}-cloudfront-edge-access"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
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
