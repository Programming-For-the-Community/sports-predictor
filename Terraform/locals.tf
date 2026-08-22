# Naming convention shared between IAM policies (in iam-*.tf) and the
# S3/DynamoDB resources they reference -- keeps IAM scoping and resource
# names from drifting apart, since IAM policies below reference these as
# plain ARN strings rather than resource references.
locals {
  raw_bucket_name         = "${var.project}-raw-data-lake-${var.account_id}"
  model_artifacts_bucket  = "${var.project}-model-artifacts-${var.account_id}"
  frontend_bucket         = "${var.project}-frontend-${var.account_id}"
  entities_table          = "${var.project}-entities"
  events_table            = "${var.project}-events"
  player_game_stats_table = "${var.project}-player-game-stats"
  team_game_stats_table   = "${var.project}-team-game-stats"
  predictions_table       = "${var.project}-predictions"
  sport_registry_table    = "${var.project}-sport-registry"

  # The one public domain for the whole app -- CloudFront (cloudfront.tf)
  # serves the frontend at the default behavior and the API at /nfl/*
  # (path-routed to API Gateway).
  domain = "${var.project}.${var.domain_name}"

  # API Gateway's own custom domain (acm-api-gateway.tf,
  # api-gateway-domain.tf) -- CloudFront's origin, not user-facing traffic
  # (that stays on `domain` above). Project-name-first, not
  # "api.${domain}" (which would put "api" as the leftmost label) --
  # var.domain_name is shared across other projects on the same root
  # domain, so scoping "api" under this project's own name first avoids
  # colliding with another project's own api subdomain.
  api_domain = "${var.project}.api.${var.domain_name}"

  common_tags = {
    Owner       = var.owner
    Project     = var.project
    Environment = var.environment
  }
}
