# Single public entry point for the whole app -- the frontend (default
# behavior, S3 origin) and every sport's API (path-routed to API Gateway's
# own execute-api endpoint, one shared REST API for all sports), both
# under local.domain. See acm.tf/route53.tf for the matching cert/DNS
# records.
#
# One list entry per active sport's API prefix -- a path not in this list
# falls through to the default behavior (the frontend's S3 origin)
# instead of reaching API Gateway at all. File-scoped locals block, same
# convention as scheduler-nfl-train-player-prop-model.tf's own
# nfl_player_prop_stats.
locals {
  api_path_prefixes = ["nfl", "ncaafb", "nba"]
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.project}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Baseline security headers on every response -- HSTS, MIME-sniffing
# protection, clickjacking protection, and a conservative Referrer-Policy.
# Free (a native CloudFront feature, no Lambda@Edge/CloudFront Function
# needed) -- added 2026-08-14 as part of a security review, since the
# distribution previously set none of these.
#
# No Content-Security-Policy here -- the Flutter Web build's own script/
# style loading pattern hasn't been audited against a CSP yet, and a wrong
# CSP silently breaks the app in a way that's hard to notice from the
# Terraform side. Worth adding once that audit happens, not guessed here.
resource "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "${var.project}-security-headers"

  security_headers_config {
    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 63072000 # 2 years, the value HSTS preload lists expect
      include_subdomains         = true
      preload                    = true
      override                   = true
    }

    xss_protection {
      protection = true
      mode_block = true
      override   = true
    }
  }
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
    origin_id   = "api"
    domain_name = "${aws_api_gateway_rest_api.main.id}.execute-api.${var.region}.amazonaws.com"
    # Prefixes every forwarded request with the deployed stage, so a
    # public request to /nfl/... or /ncaafb/... reaches the API at
    # /<stage>/nfl/... etc., exactly what the default execute-api
    # endpoint expects.
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
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id
  }

  dynamic "ordered_cache_behavior" {
    for_each = local.api_path_prefixes
    content {
      path_pattern           = "/${ordered_cache_behavior.value}/*"
      target_origin_id       = "api"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS"]
      cached_methods         = ["GET", "HEAD"]
      # Managed-CachingDisabled -- predictions are always dynamic.
      cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id
      # Managed-AllViewerExceptHostHeader -- forwards Authorization + all
      # query strings (needed for the Cognito authorizer and player-prop
      # `?stat=` params), same as Managed-AllViewer, but substitutes the
      # origin's own domain as the Host header instead of forwarding the
      # viewer's. API Gateway's regional execute-api endpoint rejects a
      # Host header that doesn't match its own domain, so plain AllViewer
      # 403s every request to this origin. This same managed policy also
      # forwards CloudFront's device-type/viewer-location headers (per
      # AWS's own docs for it) -- library/serving/viewer_analytics.py reads
      # those from inside predict-read, no separate plumbing needed for
      # that data to arrive.
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
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

  # US-only -- this is a single-user project with no expected traffic from
  # outside the US; every request from elsewhere is a scan/bot/probe by
  # construction, not a legitimate user this app needs to reach. Free,
  # native CloudFront feature (no WAF needed for this specific control).
  # Added 2026-08-14 as part of a security review.
  restrictions {
    geo_restriction {
      restriction_type = "whitelist"
      locations        = ["US"]
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.api.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "frontend"
  })
}
