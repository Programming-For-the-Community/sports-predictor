# Registers the custom domain on the regional API Gateway endpoint and maps
# the stage to it. With an empty base_path the custom domain routes directly
# to the API root -- callers hit api.yourdomain.com/predict rather than
# api.yourdomain.com/development/predict.
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

resource "aws_api_gateway_base_path_mapping" "api" {
  api_id      = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.main.stage_name
  domain_name = aws_api_gateway_domain_name.api.domain_name
  # base_path omitted -- maps the domain root directly to the API stage
}
