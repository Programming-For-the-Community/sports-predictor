# Lambda observability dashboard (invocations/errors/duration/concurrency/
# throttles across every Lambda in this project), added 2026-08-16 per a
# direct user ask -- distinct from cloudwatch-dashboard-viewer-analytics.tf's
# Logs Insights-based request/visitor breakdown, this one is plain AWS/Lambda
# namespace metrics.
#
# Built entirely with CloudWatch metric-math SEARCH() expressions, not
# explicit per-function metric references -- there's no `depends_on`/
# resource reference to any of the 19 aws_lambda_function resources at all.
# Each function name already follows "${var.project}-<sport>-<component>"
# (or "${var.project}-season-gate" for the one shared, non-per-sport
# function), so a SEARCH scoped to a literal substring term is enough to
# group by sport, and one scoped to just the project prefix covers every
# function for the by-function views. This also means a newly onboarded
# sport's Lambdas (e.g. Sub-phase 3B's ncaambb-*) show up in the by-sport
# rollups automatically the moment this file adds its own search term --
# and in the by-function views with ZERO changes here at all.
#
# "By sport" widgets aggregate each sport's own N functions into one line
# via SUM(SEARCH(...))/AVG(SEARCH(...)) metric math -- a single combined
# trend per sport. "By function" widgets use a raw (unwrapped) SEARCH so
# each of the 19 functions renders as its own series, for spotting which
# specific Lambda is actually driving a spike. Concurrency's by-sport
# rollup is a SUM of each function's own period Maximum -- an approximate
# upper bound (summed maxima don't necessarily co-occur at the exact same
# instant), same simplification any SUM-of-SEARCH concurrency rollup makes;
# fine for spotting real single-sport concurrency pressure, not exact.
locals {
  lambda_dashboard_sports = ["nfl", "ncaafb", "nba"]
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
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Invocations\" \"${var.project}-${sport}-\"', 'Sum', 300))"
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
                expression = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Invocations\" \"${var.project}-\"', 'Sum', 300)"
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
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Errors\" \"${var.project}-${sport}-\"', 'Sum', 300))"
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
                expression = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Errors\" \"${var.project}-\"', 'Sum', 300)"
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
                expression = "AVG(SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Duration\" \"${var.project}-${sport}-\"', 'Average', 300))"
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
                expression = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Duration\" \"${var.project}-\"', 'Average', 300)"
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
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} MetricName=\"ConcurrentExecutions\" \"${var.project}-${sport}-\"', 'Maximum', 300))"
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
                expression = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"ConcurrentExecutions\" \"${var.project}-\"', 'Maximum', 300)"
                id         = "conc_all"
              }
            ]
          ]
        }
      },

      # --- Row 5: Throttles -- a concurrency-limit symptom, not one of the
      # user's 4 named metrics, but directly related to "concurrent
      # invocations" and cheap to add alongside it (same SEARCH pattern,
      # zero new infrastructure) -- see project-api-gateway-429-thundering-
      # herd-fix memory for why concurrency pressure is a real, previously-
      # hit failure mode in this project, not a hypothetical one.
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
                expression = "SUM(SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Throttles\" \"${var.project}-${sport}-\"', 'Sum', 300))"
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
                expression = "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Throttles\" \"${var.project}-\"', 'Sum', 300)"
                id         = "thr_all"
              }
            ]
          ]
        }
      },
    ]
  })
}
