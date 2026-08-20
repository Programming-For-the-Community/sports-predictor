# Viewer analytics dashboard: location/browser/device/endpoint breakdown,
# built from Logs Insights queries against each sport's predict-read
# Lambda log group. CloudFront's AllViewerExceptHostHeader origin request
# policy forwards device-type/viewer-location headers to the API origin,
# which predict-read logs directly.
#
# Logs Insights `parse` patterns below match by substring, not field
# order. Fields that can be null for some IPs (city, region_name) are
# excluded from that row's stats rather than mislabeled "Unknown".
locals {
  viewer_analytics_log_sources = join(" | ", [
    for lg in [
      aws_cloudwatch_log_group.nfl_predict_read.name,
      aws_cloudwatch_log_group.ncaafb_predict_read.name,
      aws_cloudwatch_log_group.nba_predict_read.name,
    ] : "SOURCE '${lg}'"
  ])
}

resource "aws_cloudwatch_dashboard" "viewer_analytics" {
  dashboard_name = "${var.project}-viewer-analytics"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "log"
        x      = 0
        y      = 0
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
        y      = 0
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
        y      = 6
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
        y      = 6
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
        y      = 12
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Top raw User-Agent strings"
          view   = "table"
          query  = <<-QUERY
            ${local.viewer_analytics_log_sources}
            | filter @message like /viewer_analytics/
            | parse @message '"user_agent": "*"' as user_agent
            | stats count(*) as requests by user_agent
            | sort requests desc
            | limit 20
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 12
        width  = 12
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
        y      = 18
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
      # (cloudfront-standard-logging.tf) -- requests blocked by
      # geo-restriction, which never reach any Lambda. No city field;
      # CloudFront only resolves country for a blocked request.
      {
        type   = "text"
        x      = 0
        y      = 28
        width  = 24
        height = 2
        properties = {
          markdown = "## Turned away (blocked) traffic\nSourced from CloudFront's own edge logs, not a Lambda -- includes requests blocked by geo-restriction before they ever reached the app."
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 30
        width  = 12
        height = 6
        properties = {
          # us-east-1, where cloudfront_edge_access_logs lives
          region = "us-east-1"
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
        y      = 30
        width  = 12
        height = 6
        properties = {
          region = "us-east-1" # where cloudfront_edge_access_logs lives
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
        y      = 36
        width  = 24
        height = 8
        properties = {
          region = "us-east-1" # where cloudfront_edge_access_logs lives
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
    ]
  })
}
