# Single public entry point for the whole app -- the frontend (default
# behavior, S3 origin) and the existing NFL API (path-routed to API
# Gateway's own execute-api endpoint), both under local.domain. Replaces
# API Gateway's own custom-domain feature (formerly api-gateway-domain.tf)
# -- see acm.tf/route53.tf for the matching cert/DNS changes.
#
# One list entry per active sport's API prefix -- adding a second sport's
# API later is one more entry here, not a hand-written cache behavior.
# File-scoped locals block, same convention as
# scheduler-nfl-train-player-prop-model.tf's own nfl_player_prop_stats.
locals {
  api_path_prefixes = ["nfl"]
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.project}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  default_root_object = "index.html"
  aliases             = [local.domain]

  origin {
    origin_id                = "frontend-s3"
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    origin_id   = "nfl-api"
    domain_name = "${aws_api_gateway_rest_api.main.id}.execute-api.${var.region}.amazonaws.com"
    # Prefixes every forwarded request with the deployed stage, so a
    # public request to /nfl/... reaches the API at /<stage>/nfl/...,
    # exactly what the default execute-api endpoint expects.
    origin_path = "/${aws_api_gateway_stage.main.stage_name}"

    custom_origin_config {
      origin_protocol_policy = "https-only"
      http_port              = 80
      https_port             = 443
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "frontend-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    # Managed-CachingOptimized -- static assets, safe to cache aggressively.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  dynamic "ordered_cache_behavior" {
    for_each = local.api_path_prefixes
    content {
      path_pattern           = "/${ordered_cache_behavior.value}/*"
      target_origin_id       = "nfl-api"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS"]
      cached_methods         = ["GET", "HEAD"]
      # Managed-CachingDisabled -- predictions are always dynamic.
      cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      # Managed-AllViewer -- forwards Authorization + all query strings,
      # needed for the Cognito authorizer and player-prop `?stat=` params.
      origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
    }
  }

  # go_router's client-side routing means a hard refresh/deep link (e.g.
  # /nfl/events) has no matching S3 object -- S3 returns 403 (no public
  # ListBucket) which CloudFront remaps to index.html so the Flutter app
  # boots and its own router takes over.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.api.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}
