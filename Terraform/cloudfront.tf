# Single public entry point for the app: frontend (default behavior, S3
# origin) and every sport's API (path-routed to API Gateway's shared REST
# API), both under local.domain.
#
# One entry per active sport's API prefix; a path not listed falls through
# to the frontend's S3 origin instead of reaching API Gateway.
locals {
  api_path_prefixes = ["nfl", "ncaafb", "nba", "ncaambb", "pga", "f1"]
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
# protection, clickjacking protection, a conservative Referrer-Policy, a
# Content-Security-Policy and a Permissions-Policy.
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

    # "X-XSS-Protection: 0" -- the legacy browser filter is itself
    # exploitable; the Content-Security-Policy below replaces it.
    xss_protection {
      protection = false
      override   = true
    }

    content_security_policy {
      content_security_policy = local.content_security_policy
      override                = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
      override = true
    }
  }
}

locals {
  # Flutter web loads CanvasKit (JS + WebAssembly) from www.gstatic.com and
  # sets inline <style> elements; google_fonts loads from the two Google
  # Fonts hosts; the app calls Cognito directly and everything else
  # same-origin through this distribution.
  content_security_policy = join("; ", [
    "default-src 'self'",
    "script-src 'self' 'wasm-unsafe-eval' https://www.gstatic.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "connect-src 'self' https://cognito-idp.${var.region}.amazonaws.com https://www.gstatic.com https://fonts.gstatic.com https://fonts.googleapis.com",
    "img-src 'self' data: blob:",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
  ])
}

# /app/* (the Android APK) answers Android user agents only: Android
# browsers name Android in theirs, and the app's own clients send
# "SportsPredictor (Linux; Android)" (front-end/lib/core/mobile/app_release.dart).
# Runs at viewer request, ahead of the cache, so every request is checked.
resource "aws_cloudfront_function" "android_only" {
  name    = "${var.project}-android-only"
  runtime = "cloudfront-js-2.0"
  comment = "Serves /app/* to Android user agents only"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var ua = event.request.headers['user-agent'];
      if (ua && ua.value.indexOf('Android') !== -1) return event.request;
      return { statusCode: 403, statusDescription: 'Forbidden' };
    }
  EOT
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  default_root_object = "index.html"
  aliases             = [local.domain]
  # Blocks known-malicious IPs via AWS's own reputation list --
  # waf-cloudfront.tf.
  web_acl_id = aws_wafv2_web_acl.cloudfront.arn

  origin {
    origin_id                = "frontend-s3"
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    origin_id                = "mobile-releases-s3"
    domain_name              = aws_s3_bucket.mobile_releases.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    origin_id = "api"
    # api-gateway-domain.tf's custom domain, not the raw
    # execute-api.<region>.amazonaws.com hostname -- that default endpoint
    # is disabled entirely (disable_execute_api_endpoint in api-gateway.tf)
    # since it can't be restricted below TLS 1.0. No origin_path stage
    # prefix needed here anymore either -- the custom domain's own base
    # path mapping (aws_api_gateway_base_path_mapping.main) already routes
    # its root straight to the deployed stage.
    domain_name = aws_api_gateway_domain_name.main.domain_name

    custom_origin_config {
      origin_protocol_policy = "https-only"
      http_port              = 80
      https_port             = 443
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    # Proves to the API's regional WAF (waf-api-gateway.tf) that the
    # request came through this distribution.
    custom_header {
      name  = local.cloudfront_origin_secret_header
      value = random_password.cloudfront_origin_secret.result
    }
  }

  default_cache_behavior {
    target_origin_id           = "frontend-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.frontend_edge.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id
    # The web build is uploaded uncompressed; CloudFront gzips/brotlis it.
    compress = true
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

  # The APK is uploaded with `Cache-Control: no-cache`, so frontend_edge's
  # 0s default TTL makes every request revalidate -- a new release is
  # visible immediately, and HEAD requests return its current metadata.
  ordered_cache_behavior {
    path_pattern               = "/app/*"
    target_origin_id           = "mobile-releases-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.frontend_edge.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.android_only.arn
    }
  }

  # go_router's client-side routing means a deep link (e.g. /nfl/events)
  # has no matching S3 object; CloudFront remaps S3's 404 to index.html so
  # the Flutter app boots and its own router takes over.
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  # Every 403 CloudFront returns to the viewer -- geo-restriction and WAF
  # blocks (waf-cloudfront.tf) alike -- rewritten to one minimal,
  # reason-free page (front-end/web/403.html). Stays a real 403, not
  # remapped to 200 like the 404 entry above. API Gateway's own 4xx
  # family (api-gateway.tf) is normalized separately, at the origin.
  custom_error_response {
    error_code         = 403
    response_code      = 403
    response_page_path = "/403.html"
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
