# Application-level activity: Lambda invocations/errors/duration/
# concurrency/throttles (AWS/Lambda metrics) and viewer analytics
# (Logs Insights against the predict-read log group). Merges what were 2
# separate dashboards (lambda-observability, viewer-analytics) into one.
locals {
  # One shared predict-read Lambda now, not 6 (2026-09-19) -- still a list
  # (not a bare string) since lambda-cloudwatch-geo-widget.tf's own
  # StartQuery calls and viewer_analytics_log_sources below both consume
  # this as a list.
  viewer_analytics_log_group_names = [
    aws_cloudwatch_log_group.predict_read.name,
  ]

  # Dashboard-widget queries have no separate log-group field for a
  # multi-source query -- SOURCE is embedded in the query text itself.
  # lambda-cloudwatch-geo-widget.tf's own StartQuery calls pass
  # viewer_analytics_log_group_names directly instead.
  viewer_analytics_log_sources = join(" | ", [for lg in local.viewer_analytics_log_group_names : "SOURCE '${lg}'"])
}

resource "aws_cloudwatch_dashboard" "application" {
  dashboard_name = "${var.project}-application"

  dashboard_body = jsonencode({
    widgets = [
      # --- Lambda ---
      {
        type       = "text", x = 0, y = 0, width = 24, height = 1
        properties = { markdown = "## Lambda" }
      },
      {
        type   = "metric"
        x      = 0
        y      = 1
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Invocations by function"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/Lambda,FunctionName} Invocations ${var.project}-', 'Sum', 300)", id = "inv_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 7
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Errors by function"
          view    = "bar"
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/Lambda,FunctionName} Errors ${var.project}-', 'Sum', 300)", id = "err_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 13
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Average duration by function (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/Lambda,FunctionName} Duration ${var.project}-', 'Average', 300)", id = "dur_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 19
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Concurrent executions by function"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/Lambda,FunctionName} ConcurrentExecutions ${var.project}-', 'Maximum', 300)", id = "conc_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 25
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Throttles by function"
          view    = "bar"
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/Lambda,FunctionName} Throttles ${var.project}-', 'Sum', 300)", id = "thr_all" }]]
        }
      },

      # --- Viewer analytics ---
      {
        type       = "text", x = 0, y = 31, width = 24, height = 1
        properties = { markdown = "## Viewer analytics" }
      },
      {
        type   = "log"
        x      = 0
        y      = 32
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Requests by sport"
          view   = "pie"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"sport": "*"' as sport
            | stats count(*) as requests by sport
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 32
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Requests by country"
          view   = "bar"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"country_name": "*"' as country_name
            | stats count(*) as requests by country_name
            | sort requests desc
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 38
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Device type (mobile / tablet / desktop / smart TV)"
          view   = "bar"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"is_mobile": "*"' as is_mobile
            | parse @message '"is_tablet": "*"' as is_tablet
            | parse @message '"is_desktop": "*"' as is_desktop
            | parse @message '"is_smarttv": "*"' as is_smarttv
            | stats sum(is_mobile = "true") as mobile, sum(is_tablet = "true") as tablet, sum(is_desktop = "true") as desktop, sum(is_smarttv = "true") as smart_tv
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 38
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "OS (iOS / Android)"
          view   = "bar"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"is_ios": "*"' as is_ios
            | parse @message '"is_android": "*"' as is_android
            | stats sum(is_ios = "true") as ios, sum(is_android = "true") as android
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 44
        width  = 24
        height = 6
        properties = {
          region = var.region
          title  = "Top API endpoints"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"method": "*"' as method
            | parse @message '"resource": "*"' as resource
            | stats count(*) as requests by method, resource
            | sort requests desc
            | limit 20
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 50
        width  = 24
        height = 10
        properties = {
          region = var.region
          title  = "Top cities"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"city": "*"' as city
            | parse @message '"region_name": "*"' as region_name
            | parse @message '"country_name": "*"' as country_name
            | stats count(*) as requests by city, region_name, country_name
            | sort requests desc
            | limit 20
          QUERY
        }
      },
      # Everything below is sourced from CloudFront's own edge logs
      # (cloudfront-standard-logging.tf), not a Lambda.
      {
        type       = "text", x = 0, y = 60, width = 24, height = 2
        properties = { markdown = "## Turned away (blocked) traffic\nSourced from CloudFront's own edge logs -- includes requests blocked by geo-restriction before they ever reached the app." }
      },
      {
        type   = "log"
        x      = 0
        y      = 62
        width  = 12
        height = 6
        properties = {
          region = "us-east-1" # where cloudfront_edge_access_logs lives
          title  = "Blocked requests by country"
          view   = "bar"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.cloudfront_edge_access_logs.name}'
            | filter `sc-status` = "403"
            | stats count(*) as requests by `c-country`
            | sort requests desc
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 62
        width  = 12
        height = 6
        properties = {
          region = "us-east-1"
          title  = "Blocked requests by attempted path"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.cloudfront_edge_access_logs.name}'
            | filter `sc-status` = "403"
            | stats count(*) as attempts by `cs-uri-stem`
            | sort attempts desc
            | limit 20
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 68
        width  = 24
        height = 8
        properties = {
          region = "us-east-1"
          title  = "Recent blocked requests"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.cloudfront_edge_access_logs.name}'
            | filter `sc-status` = "403"
            | fields date, time, `c-ip`, `c-country`, `cs-method`, `cs-uri-stem`, `x-edge-detailed-result-type`, `cs(User-Agent)`
            | sort @timestamp desc
            | limit 50
          QUERY
        }
      },
      # Custom-widget geo panels (lambda-cloudwatch-geo-widget.tf) -- no
      # native map widget type, so these render locally with Pillow.
      {
        type       = "text", x = 0, y = 76, width = 24, height = 1
        properties = { markdown = "## Geo panels" }
      },
      {
        type   = "custom"
        x      = 0
        y      = 77
        width  = 12
        height = 9
        properties = {
          endpoint = aws_lambda_function.cloudwatch_geo_widget.arn
          params   = { mode = "accepted" }
          title    = "Accepted traffic by state"
          updateOn = { refresh = true, resize = false, timeRange = true }
        }
      },
      {
        type   = "custom"
        x      = 12
        y      = 77
        width  = 12
        height = 9
        properties = {
          endpoint = aws_lambda_function.cloudwatch_geo_widget.arn
          params   = { mode = "blocked" }
          title    = "Blocked traffic by country"
          updateOn = { refresh = true, resize = false, timeRange = true }
        }
      },
      # --- Who is using the app ---
      {
        type       = "text", x = 0, y = 86, width = 24, height = 1
        properties = { markdown = "## Users -- who is signed in and what they view (username comes from the Cognito authorizer claims; requests logged before this shipped have no user)" }
      },
      {
        type   = "log"
        x      = 0
        y      = 87
        width  = 12
        height = 7
        properties = {
          region = var.region
          title  = "Requests by user"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"username": "*"' as username
            | parse @message '"sport": "*"' as sport
            | filter ispresent(username)
            | stats count(*) as requests, count_distinct(sport) as sports, max(@timestamp) as last_seen by username
            | sort requests desc
            | limit 25
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 87
        width  = 12
        height = 7
        properties = {
          region = var.region
          title  = "Daily active users"
          view   = "bar"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"username": "*"' as username
            | filter ispresent(username)
            | stats count_distinct(username) as active_users by bin(1d)
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 94
        width  = 12
        height = 7
        properties = {
          region = var.region
          title  = "Most-viewed sport per user"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"username": "*"' as username
            | filter ispresent(username)
            | parse @message '"sport": "*"' as sport
            | stats count(*) as requests by username, sport
            | sort username asc, requests desc
            | limit 50
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 94
        width  = 12
        height = 7
        properties = {
          region = var.region
          title  = "Top endpoints per user"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"username": "*"' as username
            | filter ispresent(username)
            | parse @message '"resource": "*"' as resource
            | stats count(*) as requests by username, resource
            | sort requests desc
            | limit 50
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 101
        width  = 12
        height = 7
        properties = {
          region = var.region
          title  = "Most-viewed games / pages (by concrete path)"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"path": "*"' as path
            | parse @message '"user_id": "*"' as user_id
            | filter ispresent(path)
            | stats count(*) as views, count_distinct(user_id) as viewers by path
            | sort views desc
            | limit 25
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 101
        width  = 12
        height = 7
        properties = {
          region = var.region
          title  = "Requests over time by sport"
          view   = "line"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"sport": "*"' as sport
            | stats count(*) as requests by bin(1h), sport
          QUERY
        }
      },
    ]
  })
}