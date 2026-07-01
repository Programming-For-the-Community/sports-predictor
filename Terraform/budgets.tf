# Cost allocation tags must be Active before any tag-scoped budget or Cost
# Explorer report shows data for them. AWS rejects activation for a tag key
# that has never appeared in cost and usage data, so this is off by default
# -- flip activate_cost_allocation_tags to true once the first Phase 0/1
# resources carrying these tags actually exist. See docs/TAGGING_STRATEGY.md
# for the manual console steps this replaces.
resource "aws_ce_cost_allocation_tag" "tags" {
  for_each = var.activate_cost_allocation_tags ? toset(["Project", "Sport", "Component", "Environment"]) : []

  tag_key = each.value
  status  = "Active"
}

# Comprehensive project budget -- Scoped to the Project tag so it tracks this project's
# spend specifically, not anything else sharing the AWS account.
resource "aws_budgets_budget" "project" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_limit
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "billing"
  })

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project${var.project}"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Per-sport budgets -- disabled by default (empty map, see variables.tf).
# Populate per_sport_budget_limits once a sport has enough real cost history
# to set a sane threshold against
resource "aws_budgets_budget" "per_sport" {
  for_each = var.per_sport_limits

  name         = "${var.project}-${each.key}-monthly"
  budget_type  = "COST"
  limit_amount = each.value
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  tags = merge(local.common_tags, {
    Sport     = each.key
    Component = "billing"
  })

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Sport${each.key}"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
