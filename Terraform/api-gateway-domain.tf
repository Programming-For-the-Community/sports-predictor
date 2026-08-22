# Gets API Gateway off its default execute-api.<region>.amazonaws.com
# endpoint. That default endpoint is AWS-managed and cannot be
# restricted below TLS 1.0 -- there's no security_policy control on it at
# all, unlike a custom domain name, which supports TLS_1_2. CloudFront
# itself was already TLS 1.2-only end to end (cloudfront.tf's
# minimum_protocol_version/origin_ssl_protocols), so the real gap was the
# default endpoint remaining reachable in parallel, completely bypassing
# CloudFront, with no TLS floor at all.
resource "aws_api_gateway_domain_name" "main" {
  domain_name              = local.api_domain
  regional_certificate_arn = aws_acm_certificate_validation.api_gateway.certificate_arn
  security_policy          = "TLS_1_2"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# No base_path -- the custom domain's own root maps directly to this
# stage, replacing the stage-prefix job cloudfront.tf's origin_path used
# to do against the raw execute-api endpoint.
resource "aws_api_gateway_base_path_mapping" "main" {
  api_id      = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  domain_name = aws_api_gateway_domain_name.main.domain_name
}

# CloudFront's "api" origin (cloudfront.tf) resolves this via public DNS
# same as any custom origin -- not meant for a browser to hit directly,
# though nothing stops it; real user traffic still goes through
# local.domain via CloudFront, unaffected by this record existing.
resource "aws_route53_record" "api_gateway" {
  zone_id = var.hosted_zone_id
  name    = aws_api_gateway_domain_name.main.domain_name
  type    = "A"

  alias {
    name                   = aws_api_gateway_domain_name.main.regional_domain_name
    zone_id                = aws_api_gateway_domain_name.main.regional_zone_id
    evaluate_target_health = false
  }
}
