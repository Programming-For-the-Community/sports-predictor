# API Gateway health dashboard: request volume, error rates, and latency
# for the single REST API (api-gateway.tf) every sport's routes share --
# built from AWS/ApiGateway namespace metrics.
#
# Only one aws_api_gateway_rest_api exists in this project (every sport's
# routes live under the same API, path-routed -- see design/ARCHITECTURE.md),
# so metrics are referenced directly by ApiName/Stage rather than a
# SEARCH() prefix the way the Lambda/DynamoDB dashboards discover an
# open-ended, per-sport resource set.
#
# Added 2026-08-25 specifically because there was previously ZERO
# dashboard visibility at the gateway level -- this project already had
# one real production incident here (a 429 thundering-herd from the
# event-list page's fan-out pattern combined with too-low a throttle,
# since fixed by raising the throttle and adding client-side jitter/
# retry -- see project-api-gateway-429-thundering-herd-fix memory). A
# 429 surfaces here as part of the 4XXError metric (API Gateway has no
# separate 429-specific CloudWatch metric); this dashboard is what would
# confirm that fix is holding and catch a recurrence early.
resource "aws_cloudwatch_dashboard" "api_gateway" {
  dashboard_name = "${var.project}-api-gateway"

  dashboard_body = jsonencode({
    widgets = [
      # --- Row 1: Request volume and errors ---
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Request count"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "4XX / 5XX errors (a 429 throttle shows up as 4XXError -- see this file's own comment)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ApiGateway", "4XXError", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Sum", label = "4XX" }],
            ["AWS/ApiGateway", "5XXError", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Sum", label = "5XX" }],
          ]
        }
      },

      # --- Row 2: Latency -- gateway overhead vs. the backend Lambda's own time ---
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Latency (ms) -- end to end"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ApiGateway", "Latency", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Average", label = "Average" }],
            ["AWS/ApiGateway", "Latency", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "p99", label = "p99" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Integration latency (ms) -- backend (Lambda) time only"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ApiGateway", "IntegrationLatency", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Average", label = "Average" }],
            ["AWS/ApiGateway", "IntegrationLatency", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "p99", label = "p99" }],
          ]
        }
      },
    ]
  })
}
