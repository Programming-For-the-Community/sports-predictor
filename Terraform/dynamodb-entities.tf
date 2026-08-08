# One row per team or player, regardless of sport. Sport is embedded in the
# partition key (SPORT#NFL#ENTITY#KC) so per-sport queries stay in a single
# partition rather than scanning the whole table.
#
# team-index -- every player entity's current team (team_key, kept fresh
# by roster sync's daily upsert) queried directly, for live_features.py's
# presumptive-leader candidate selection (who's actually on this team
# right now, not who logged a stat row for it in their last game). No
# range key needed -- one team's roster (~50-90 players including
# practice squad) is small enough that a single Query without further
# sorting is enough.
resource "aws_dynamodb_table" "entities" {
  name         = local.entities_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "entity_key"

  attribute {
    name = "entity_key"
    type = "S"
  }

  attribute {
    name = "team_key"
    type = "S"
  }

  global_secondary_index {
    name            = "team-index"
    hash_key        = "team_key"
    projection_type = "ALL"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
