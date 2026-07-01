# One row per event per model version (event-level outcome predictions), or
# one row per event-player-model (player-prop predictions). Both coexist in
# the same partition under the same PK (the event), separated by their SK:
#   - Event outcome:  SK = MODEL#v3
#   - Player prop:    SK = MODEL#v3#PLAYER#mahomes-patrick
#
# Kept separate from the events table so re-running a model for the same
# event appends a new SK row rather than overwriting the raw result.
#
# No GSI now -- "all predictions for player X" (across events) is a
# deferred access pattern not yet needed by the serving layer.
resource "aws_dynamodb_table" "predictions" {
  name         = local.predictions_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_key"
  range_key    = "model_key"

  attribute {
    name = "event_key"
    type = "S"
  }

  attribute {
    name = "model_key"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
