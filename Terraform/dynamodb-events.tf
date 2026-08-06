# One row per game, match, tournament, or race. The participants array
# carries team-level results for head-to-head sports (NFL/NCAA FB/NBA/NCAA
# MBB) and individual results for field-event sports (PGA Tour/F1) -- in
# the field-event case, the participant entity IS the player, so there is
# no separate player_game_stats row needed.
#
# status-index GSI serves "this week's games" (GET /nfl/events, GET
# /nfl/season) via Query(status=X) instead of a full-table Scan;
# range_key=event_date returns results in the order get_all_events wants
# (most recent first, scan_index_forward=False) with no separate sort.
#
# The entity_id GSI from docs/DATA_SCHEMA.md ("this team's full event
# history" frontend view) stays deferred -- no route needs it yet.
#
# Every item written here MUST have a non-empty event_date -- DynamoDB
# silently omits an item from a GSI projection if it's missing the GSI's
# range key attribute. normalize.py's scoreboard_event_to_event_item
# always sets event_date from the raw ESPN payload, so this isn't a
# concern in practice, just worth knowing if a future write path ever
# skips it.
resource "aws_dynamodb_table" "events" {
  name         = local.events_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_key"

  attribute {
    name = "event_key"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "event_date"
    type = "S"
  }

  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "event_date"
    projection_type = "ALL"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
