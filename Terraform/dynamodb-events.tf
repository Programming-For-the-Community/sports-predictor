# One row per game, match, tournament, or race. The participants array
# carries team-level results for head-to-head sports (NFL/NCAA FB/NBA/NCAA
# MBB) and individual results for field-event sports (PGA Tour/F1) -- in
# the field-event case, the participant entity IS the player, so there is
# no separate player_game_stats row needed.
#
# No GSI yet -- the two candidates from docs/DATA_SCHEMA.md are deferred
# until a feature actually needs them, since each GSI roughly doubles the
# write cost for this table:
#   - event_date GSI: for "this week's games" date-range queries
#   - entity_id GSI: for "this team's full event history" frontend view
resource "aws_dynamodb_table" "events" {
  name         = local.events_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_key"

  attribute {
    name = "event_key"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
