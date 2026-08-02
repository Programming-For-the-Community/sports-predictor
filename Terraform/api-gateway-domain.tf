# Registers the custom domain on the regional API Gateway endpoint.
resource "aws_api_gateway_domain_name" "api" {
  domain_name              = local.api_domain
  regional_certificate_arn = aws_acm_certificate_validation.api.certificate_arn

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "serving"
  })
}

# Connects the custom domain to the actual deployed stage -- without this,
# aws_api_gateway_domain_name above is just a registered name with a valid
# cert and DNS record (route53.tf) but nothing behind it; API Gateway
# still needs an explicit mapping to know which API+stage a request to
# that domain should reach. Empty base_path means the domain's root maps
# directly to the stage, matching how every route in
# api-gateway-nfl-predict.tf is already written (e.g. .../nfl/predictions/...
# right after the domain, no extra path segment).
resource "aws_api_gateway_base_path_mapping" "api" {
  api_id      = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  domain_name = aws_api_gateway_domain_name.api.domain_name
}
