# Single public entry point for the app: frontend (default behavior, S3
# origin) and every sport's API (path-routed to API Gateway's shared REST
# API), both under local.domain.
#
# One entry per active sport's API prefix; a path not listed falls through
# to the frontend's S3 origin instead of reaching API Gateway.
locals {
  api_path_prefixes = ["nfl", "ncaafb", "nba", "ncaambb"]
}

# Managed-CachingOptimized only respects an origin's Cache-Control when it
# carries an explicit max-age/s-maxage; s3-frontend sends a bare
# `Cache-Control: no-cache` with no max-age, which CloudFront can't turn
# into a TTL, so it falls back to its own 24h DefaultTTL. min/default TTL
# of 0 forces revalidation instead. Origin headers with an explicit
# max-age are still honored.
resource "aws_cloudfront_cache_policy" "frontend_edge" {
  name        = "${var.project}-frontend-edge"
  min_ttl     = 0
  default_ttl = 0
  max_ttl     = 31536000

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.project}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Baseline security headers on every response -- HSTS, MIME-sniffing
# protection, clickjacking protection, and a conservative Referrer-Policy.
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
    # Prefixes forwarded requests with the deployed stage, so a request to
    # /nfl/... reaches the API at /<stage>/nfl/....
    origin_path = "/${aws_api_gateway_stage.main.stage_name}"

    custom_origin_config {
      origin_protocol_policy = "https-only"
      http_port              = 80
      https_port             = 443
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "frontend-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.frontend_edge.id
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
      # Managed-AllViewerExceptHostHeader -- forwards Authorization, query
      # strings, and CloudFront's device-type/viewer-location headers, but
      # substitutes the origin's own domain as the Host header; API
      # Gateway's execute-api endpoint rejects a mismatched Host header.
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    }
  }

  # go_router's client-side routing means a deep link (e.g. /nfl/events)
  # has no matching S3 object; CloudFront remaps S3's 404 to index.html so
  # the Flutter app boots and its own router takes over.
  #
  # No 403 entry here -- custom_error_response is distribution-wide, and
  # CloudFront's geo-restriction block below also returns 403, so a
  # 403->200 rule would rewrite geo-blocked requests into a served app too.
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  # US-only; no expected traffic from outside the US for this single-user
  # project.
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
