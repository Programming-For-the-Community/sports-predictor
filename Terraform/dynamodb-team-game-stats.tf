# One row per team per event, for team sports only (NFL, NCAA FB, NBA,
# NCAA MBB) -- ESPN's team-level box score aggregate (turnovers, total
# yards, time of possession, third/fourth-down and red-zone efficiency,
# etc.). Same stat_line-as-Map pattern as player_game_stats.
#
# No entity_id/team_id GSI: feature engineering reads this the same way
# it reads events, via a full scan, not a per-team Query.
resource "aws_dynamodb_table" "team_game_stats" {
  name         = local.team_game_stats_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_key"
  range_key    = "team_key"

  attribute {
    name = "event_key"
    type = "S"
  }

  attribute {
    name = "team_key"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
