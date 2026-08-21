# Lambda observability dashboard: invocations/errors/duration/concurrency/
# throttles across every Lambda, built from AWS/Lambda namespace metrics
# (distinct from cloudwatch-dashboard-viewer-analytics.tf's Logs
# Insights-based breakdown).
#
# Uses CloudWatch metric-math SEARCH() expressions scoped by function-name
# prefix ("${var.project}-<sport>-") rather than explicit per-function
# metric references, so it covers every function without referencing each
# aws_lambda_function resource. "By sport" widgets aggregate via
# SUM/AVG(SEARCH(...)); "by function" widgets use a raw SEARCH so each
# function renders as its own series. Concurrency's by-sport rollup sums
# each function's own period Maximum, an approximate upper bound since
# summed maxima don't necessarily co-occur at the same instant.
#
# SEARCH terms (metric name and prefix) must stay unquoted -- a quoted
# term, or mixing a MetricName= clause with a plain term, matches zero
# series.
locals {
  lambda_dashboard_sports = ["nfl", "ncaafb", "nba", "ncaambb"]
}

resource "aws_cloudwatch_dashboard" "lambda_observability" {
  dashboard_name = "${var.project}-lambda-observability"

  dashboard_body = jsonencode({
    widgets = [
      # --- Row 1: Invocations ---
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Invocations by sport"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            for sport in local.lambda_dashboard_sports : [
              {
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} Invocations ${var.project}-${sport}-', 'Sum', 300))"
                label      = upper(sport)
                id         = "inv_${sport}"
              }
            ]
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
          title   = "Invocations by function"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/Lambda,FunctionName} Invocations ${var.project}-', 'Sum', 300)"
                id         = "inv_all"
              }
            ]
          ]
        }
      },

      # --- Row 2: Errors (failures) ---
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Errors by sport"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            for sport in local.lambda_dashboard_sports : [
              {
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}-${sport}-', 'Sum', 300))"
                label      = upper(sport)
                id         = "err_${sport}"
              }
            ]
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
          region = var.region
          title  = "Errors by function"
          view   = "bar"
          period = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}-', 'Sum', 300)"
                id         = "err_all"
              }
            ]
          ]
        }
      },

      # --- Row 3: Duration (runtime) ---
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Average duration by sport (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            for sport in local.lambda_dashboard_sports : [
              {
                expression = "AVG(SEARCH('{AWS/Lambda,FunctionName} Duration ${var.project}-${sport}-', 'Average', 300))"
                label      = upper(sport)
                id         = "dur_${sport}"
              }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Average duration by function (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/Lambda,FunctionName} Duration ${var.project}-', 'Average', 300)"
                id         = "dur_all"
              }
            ]
          ]
        }
      },

      # --- Row 4: Concurrent executions ---
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Concurrent executions by sport (approx., summed per-function maxima)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            for sport in local.lambda_dashboard_sports : [
              {
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} ConcurrentExecutions ${var.project}-${sport}-', 'Maximum', 300))"
                label      = upper(sport)
                id         = "conc_${sport}"
              }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Concurrent executions by function"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/Lambda,FunctionName} ConcurrentExecutions ${var.project}-', 'Maximum', 300)"
                id         = "conc_all"
              }
            ]
          ]
        }
      },

      # --- Row 5: Throttles -- a concurrency-limit symptom, related to
      # concurrent invocations. ---
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Throttles by sport"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            for sport in local.lambda_dashboard_sports : [
              {
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} Throttles ${var.project}-${sport}-', 'Sum', 300))"
                label      = upper(sport)
                id         = "thr_${sport}"
              }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Throttles by function"
          view   = "bar"
          period = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/Lambda,FunctionName} Throttles ${var.project}-', 'Sum', 300)"
                id         = "thr_all"
              }
            ]
          ]
        }
      },
    ]
  })
}
