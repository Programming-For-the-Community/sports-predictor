# Registers the custom domain on the regional API Gateway endpoint.
# The base path mapping (which connects the domain to a specific stage) is
# deferred to the Lambda pass because it requires an aws_api_gateway_stage,
# which in turn requires a deployment, which requires at least one method.
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
