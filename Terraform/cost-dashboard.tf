# Lightweight cost-center dashboard. CloudWatch billing metrics
# (AWS/Billing EstimatedCharges) only report total account/service spend --
# they can't be grouped by cost-allocation tag, so the actual Sport/Component
# breakdown still lives in Cost Explorer. This dashboard gives one place to
# see the overall spend trend at a glance and a pointer to where the
# tag-grouped breakdown actually is.
#
# Prerequisite: "Receive Billing Alerts" must be turned on once, manually, in
# Billing Preferences -- there's no Terraform resource for that account
# setting, and without it EstimatedCharges never publishes, leaving the
# metric widget empty.
resource "aws_cloudwatch_dashboard" "cost_center" {
  dashboard_name = "${var.project}-cost-center"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Total estimated account charges"
          region  = "us-east-1" # billing metrics only ever publish in us-east-1
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD"]
          ]
          period = 21600
          stat   = "Maximum"
        }
      },
      {
        type   = "text"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          markdown = join("\n", [
            "## Cost breakdown by tag",
            "",
            "CloudWatch billing metrics can't be grouped by cost-allocation tag. For the actual Sport/Component breakdown:",
            "",
            "1. Open [Cost Explorer](https://console.aws.amazon.com/cost-management/home#/cost-explorer)",
            "2. Group by **Tag -> Sport** to compare per-sport pipeline cost",
            "3. Group by **Tag -> Component** to compare ingestion/training/serving cost",
            "",
            "Requires the `Project`, `Sport`, `Component`, and `Environment` cost allocation tags to be Active -- see `docs/TAGGING_STRATEGY.md`."
          ])
        }
      }
    ]
  })
}
