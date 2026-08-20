# TLS certificate for the app's one public domain (frontend + API, both
# served through CloudFront -- see cloudfront.tf). Requested in us-east-1
# regardless of the stack's primary region -- a CloudFront requirement --
# via the aws.us_east_1 provider alias.
#
# DNS validation is automatic via the Route 53 hosted zone (route53.tf).
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
