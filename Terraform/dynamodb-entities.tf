# One row per team or player, regardless of sport. Sport is embedded in the
# partition key (SPORT#NFL#ENTITY#KC) so per-sport queries stay in a single
# partition rather than scanning the whole table.
#
# No GSI -- "show me all teams/players for sport X" is a Scan with a
# filter. The entity set is small (hundreds of teams, thousands of players
# across six sports), so a scan is acceptable (see docs/DATA_SCHEMA.md).
resource "aws_dynamodb_table" "entities" {
  name         = local.entities_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "entity_key"

  attribute {
    name = "entity_key"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
