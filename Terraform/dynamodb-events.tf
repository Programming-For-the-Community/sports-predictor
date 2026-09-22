# One row per game, match, tournament, or race. The participants array
# carries team-level results for head-to-head sports (NFL/NCAA FB/NBA/NCAA
# MBB) and individual results for field-event sports (PGA Tour/F1) -- in
# the field-event case, the participant entity IS the player, so there is
# no separate player_game_stats row needed.
#
# sport-status-index GSI: an earlier status-index (hash_key=status alone)
# was keyed across ALL sports, so get_all_events/get_events_by_status (the
# only two callers) were querying it by status only and then discarding
# every item whose `sport` didn't match in Python -- with 3 sports sharing
# one table on a full rest-of-season lookahead, a "scheduled" query could
# pull thousands of other sports' rows just to throw them away, and
# predict-read's own CloudWatch logs showed exactly this: some warm
# invocations under 300ms, others 26-28s with no application log line in
# between, tracking how much unrelated data happened to be in that status
# bucket at read time. This GSI is keyed by sport_status (e.g.
# "nba#scheduled", written by PipelineStorage.upsert_event) so both
# callers can Query it directly with no post-filter and no wasted read/
# transfer. status-index itself was removed once nothing queried it
# anymore -- every caller had already moved to this one.
#
# Every item written here must have a non-empty event_date -- DynamoDB
# silently omits an item from a GSI projection if it's missing the GSI's
# range key attribute. Same applies to sport_status.
resource "aws_dynamodb_table" "events" {
  name         = local.events_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_key"

  attribute {
    name = "event_key"
    type = "S"
  }

  attribute {
    name = "event_date"
    type = "S"
  }

  attribute {
    name = "sport_status"
    type = "S"
  }

  global_secondary_index {
    name            = "sport-status-index"
    hash_key        = "sport_status"
    range_key       = "event_date"
    projection_type = "ALL"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
