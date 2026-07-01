# Drives the Step Functions Map state in Phase 4 -- one row per sport,
# storing the adapter module reference, polling cadence, current model
# version, and an active flag. Created now because an empty PAY_PER_REQUEST
# table costs nothing, and creating it in Phase 4 would mean a Terraform
# change just to add one table after everything else is already deployed.
#
# See docs/DATA_SCHEMA.md (Sport registry table section) and
# docs/PROJECT_PLAN.md Phase 4 for the full context on when and how this
# table gets populated.
resource "aws_dynamodb_table" "sport_registry" {
  name         = local.sport_registry_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sport_key"

  attribute {
    name = "sport_key"
    type = "S"
  }

  deletion_protection_enabled = var.environment == "production"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "storage"
  })
}
