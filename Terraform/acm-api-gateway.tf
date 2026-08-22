# TLS certificate for API Gateway's own regional custom domain
# (api-gateway-domain.tf) -- separate from acm.tf's certificate, which is
# forced into us-east-1 by CloudFront and can't be reused here. A
# REGIONAL API Gateway custom domain's certificate must be issued in the
# same region as the API itself (var.region, matching api-gateway.tf's
# endpoint_configuration.types = ["REGIONAL"]), so this uses the default
# (unaliased) provider rather than aws.us_east_1.
#
# Exists to get API Gateway off its default execute-api.amazonaws.com
# endpoint, which can't be restricted below TLS 1.0 -- see
# api-gateway-domain.tf's own comment for the full picture.
resource "aws_acm_certificate" "api_gateway" {
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

resource "aws_route53_record" "api_gateway_acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.api_gateway.domain_validation_options : dvo.domain_name => dvo
  }

  allow_overwrite = true
  name            = each.value.resource_record_name
  records         = [each.value.resource_record_value]
  ttl             = 60
  type            = each.value.resource_record_type
  zone_id         = var.hosted_zone_id
}

resource "aws_acm_certificate_validation" "api_gateway" {
  certificate_arn         = aws_acm_certificate.api_gateway.arn
  validation_record_fqdns = [for record in aws_route53_record.api_gateway_acm_validation : record.fqdn]
}
