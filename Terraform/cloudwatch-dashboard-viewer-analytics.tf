# Viewer analytics dashboard (location/browser/device/endpoint breakdown),
# added 2026-08-14 per a security review ask; endpoint (method + resource)
# breakdown added same day. Built from Logs Insights queries
# against each sport's own predict-read Lambda log group -- there's no
# separate CloudFront access-log delivery pipeline for this (see
# library/serving/viewer_analytics.py's own docstring for why: CloudFront's
# AllViewerExceptHostHeader origin request policy already forwards its
# device-type/viewer-location headers to the API origin, so predict-read
# logs them directly into its own log group, which Lambda already creates
# with zero new delivery infrastructure).
#
# This is the account's first aws_cloudwatch_dashboard resource -- free
# (the first 3 dashboards per account have no charge).
#
# Logs Insights `parse` patterns below rely on json.dumps' field order
# being stable (Python dicts preserve insertion order) but don't actually
# depend on order -- each parse is an independent substring match, so
# reordering log_viewer_analytics' dict in the future won't break these.
# Fields that can be null for some IPs (city, region_name -- see AWS's own
# geolocation-coverage caveat) simply don't match those rows' parse, which
# excludes them from that widget rather than mislabeling them "Unknown".
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
        width  = 8
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
        x      = 8
        y      = 0
        width  = 8
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
        x      = 16
        y      = 0
        width  = 8
        height = 6
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
          title  = "OS (iOS / Android) -- CloudFront's device headers don't expose Windows/macOS/Linux specifically"
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
          title  = "Top raw User-Agent strings (browser proxy -- not pre-bucketed into named browsers)"
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
          title  = "Top API endpoints (method + resource)"
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
    ]
  })
}
