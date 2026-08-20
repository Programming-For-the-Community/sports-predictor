# Tags must be Active before any tag-scoped budget/Cost Explorer report shows
# data; AWS rejects activation for a tag key with no cost/usage history yet.
resource "aws_ce_cost_allocation_tag" "tags" {
  for_each = var.activate_cost_allocation_tags ? toset(["Project", "Sport", "Component", "Environment"]) : []

  tag_key = each.value
  status  = "Active"
}

# Scoped to the Project tag so it tracks only this project's spend.
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
    values = [format("user:Project$%s", var.project)]
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

# Per-sport budgets, disabled by default via an empty map.
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
    values = [format("user:Project$%s", var.project)]
  }

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Sport$%s", each.key)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
