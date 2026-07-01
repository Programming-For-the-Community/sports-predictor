# Read the existing hosted zone for the domain -- this is a data source only,
# it does not create or modify the zone itself.
data "aws_route53_zone" "main" {
  name         = var.domain_name
  private_zone = false
}

# A-Record alias for the API custom domain. Alias records have no TTL and
# automatically follow any changes to the API Gateway regional IP.
resource "aws_route53_record" "api" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = local.api_domain
  type    = "A"

  alias {
    name                   = aws_api_gateway_domain_name.api.regional_domain_name
    zone_id                = aws_api_gateway_domain_name.api.regional_zone_id
    evaluate_target_health = false
  }
}
