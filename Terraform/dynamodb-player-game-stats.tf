# One row per player per event, for team sports only (NFL, NCAA FB, NBA,
# NCAA MBB). Field-event sports (PGA Tour, F1) don't write here -- their
# individual results already live in events.participants since the entity
# and the competitor are the same thing.
#
# DIFFERENT STATS PER SPORT: this table uses one schema for all four team
# sports. The stat_line attribute is a DynamoDB Map, meaning it holds any
# key-value pairs an adapter writes -- a football box score ("passing_yards",
# "rushing_tds", "sacks") and a basketball box score ("points", "rebounds",
# "assists", "fg_pct") have completely different keys but fit in the same
# attribute type. No separate tables per sport are needed; the Map type
# already handles the variation at zero schema cost.
#
# entity-history GSI: the primary key is event-first (PK =
# SPORT#...#EVENT#...), so "this player's last N games" -- the core input
# to every player-prop feature -- needs a Query on entity_id, not the
# base table. event_date is stored as ISO 8601 so it can serve as the
# range key; ScanIndexForward=false then gives "most recent first" with
# no extra sorting.
#
# sport-index GSI: get_all_player_game_stats(sport) (feature engineering's
# whole-history pull, and predict/handler.py's season leaderboards) Queries
# this by sport instead of scanning the whole table.
resource "aws_dynamodb_table" "player_game_stats" {
  name         = local.player_game_stats_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_key"
  range_key    = "player_key"

  attribute {
    name = "event_key"
    type = "S"
  }

  attribute {
    name = "player_key"
    type = "S"
  }

  attribute {
    name = "entity_id"
    type = "S"
  }

  attribute {
    name = "event_date"
    type = "S"
  }

  attribute {
    name = "sport"
    type = "S"
  }

  global_secondary_index {
    name            = "entity-history"
    hash_key        = "entity_id"
    range_key       = "event_date"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "sport-index"
    hash_key        = "sport"
    range_key       = "event_date"
    projection_type = "ALL"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
