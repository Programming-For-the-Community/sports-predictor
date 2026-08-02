# TLS certificate for the app's one public domain (frontend + API, both
# served through CloudFront -- see cloudfront.tf). Must be requested in
# us-east-1 regardless of the stack's primary region (var.region is
# us-east-2) -- this is a hard CloudFront requirement, unlike a regional
# API Gateway custom domain (the previous setup), which needed the cert in
# its own region. Uses the aws.us_east_1 provider alias (main.tf).
#
# DNS validation is automatic because the hosted zone is managed in Route 53
# (see route53.tf). The validation CNAME records are created here alongside
# the certificate so the dependency chain is explicit: cert -> CNAME ->
# validation resource -> CloudFront distribution -> DNS alias.
resource "aws_acm_certificate" "api" {
  provider = aws.us_east_1

  domain_name       = local.domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

resource "aws_route53_record" "acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.api.domain_validation_options : dvo.domain_name => dvo
  }

  allow_overwrite = true
  name            = each.value.resource_record_name
  records         = [each.value.resource_record_value]
  ttl             = 60
  type            = each.value.resource_record_type
  zone_id         = var.hosted_zone_id
}

resource "aws_acm_certificate_validation" "api" {
  provider = aws.us_east_1

  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [for record in aws_route53_record.acm_validation : record.fqdn]
}
