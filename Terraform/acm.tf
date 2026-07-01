# TLS certificate for the API custom domain. Regional endpoint means the
# cert must be in the same region as the API Gateway (var.region / us-east-2)
# -- if this were an edge-optimized endpoint it would have to be in us-east-1
# regardless of where the API lives.
#
# DNS validation is automatic because the hosted zone is managed in Route 53
# (see route53.tf). The validation CNAME records are created here alongside
# the certificate so the dependency chain is explicit: cert → CNAME →
# validation resource → domain name → base path mapping.
resource "aws_acm_certificate" "api" {
  domain_name       = local.api_domain
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
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [for record in aws_route53_record.acm_validation : record.fqdn]
}
