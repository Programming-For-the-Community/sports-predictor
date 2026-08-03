# A-Record alias for the app's one public domain (frontend + API, both via
# CloudFront -- see cloudfront.tf). Alias records have no TTL and
# automatically follow any changes to the distribution's underlying IPs.
resource "aws_route53_record" "api" {
  zone_id = var.hosted_zone_id
  name    = local.domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.main.domain_name
    zone_id                = aws_cloudfront_distribution.main.hosted_zone_id
    evaluate_target_health = false
  }
}
