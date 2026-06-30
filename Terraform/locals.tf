# Naming convention shared between IAM policies (in iam-*.tf) and the
# S3/DynamoDB resources that will be created in a later pass. Defined here
# first so IAM scoping and the eventual resource names can't drift apart --
# the buckets and tables below don't exist as Terraform resources yet, but
# IAM policies are just ARN strings, so referencing them ahead of time is
# safe as long as whatever creates them later uses these same names.
locals {
  raw_bucket_name         = "${var.project}-raw-data-lake"
  model_artifacts_bucket  = "${var.project}-model-artifacts"
  frontend_bucket         = "${var.project}-frontend"
  entities_table          = "${var.project}-entities"
  events_table            = "${var.project}-events"
  player_game_stats_table = "${var.project}-player-game-stats"
  predictions_table       = "${var.project}-predictions"

  common_tags = {
    Project     = var.project
    Environment = var.environment
  }
}
