# Alarm-state dashboard for cloudwatch-alarms.tf's 25 alarm resources (18
# logical concerns -- Lambda Throttles/Duration/DynamoDB throttles each
# split into several narrower alarms to stay under PutMetricAlarm's
# 10-metric_query cap, see that file's own top comment) -- the first
# alarm-widget dashboard in this repo (every other cloudwatch-dashboard-*.tf
# file is metric-only, since no alarm resources existed to point at before
# this). Critical (pages ops_alerts) up top, Warning (dashboard-only, never
# pages) below -- same two-tier split cloudwatch-alarms.tf's own top
# comment documents.
resource "aws_cloudwatch_dashboard" "alerts" {
  dashboard_name = "${var.project}-alerts"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 1
        properties = {
          markdown = "## Critical -- pages ${var.alert_email}"
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 1
        width  = 8
        height = 4
        properties = {
          title  = "Predict Lambda Errors"
          alarms = [aws_cloudwatch_metric_alarm.predict_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 1
        width  = 8
        height = 4
        properties = {
          title  = "Predict-read Lambda Errors"
          alarms = [aws_cloudwatch_metric_alarm.predict_read_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 1
        width  = 8
        height = 4
        properties = {
          title  = "DynamoDB SystemErrors"
          alarms = [aws_cloudwatch_metric_alarm.dynamodb_system_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 5
        width  = 8
        height = 4
        properties = {
          title  = "Training Orchestrator: Execution Failed"
          alarms = [aws_cloudwatch_metric_alarm.training_orchestrator_failed.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 5
        width  = 8
        height = 4
        properties = {
          title  = "Training Orchestrator: Execution Timed Out"
          alarms = [aws_cloudwatch_metric_alarm.training_orchestrator_timed_out.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 5
        width  = 8
        height = 4
        properties = {
          title  = "API Gateway 5XX"
          alarms = [aws_cloudwatch_metric_alarm.api_gateway_5xx.arn]
        }
      },

      {
        type   = "text"
        x      = 0
        y      = 9
        width  = 24
        height = 1
        properties = {
          markdown = "## Warning -- dashboard-only, never pages"
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 10
        width  = 8
        height = 4
        properties = {
          title  = "Ingest Lambda Errors"
          alarms = [aws_cloudwatch_metric_alarm.ingest_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 10
        width  = 8
        height = 4
        properties = {
          title  = "Normalize Lambda Errors"
          alarms = [aws_cloudwatch_metric_alarm.normalize_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 10
        width  = 8
        height = 4
        properties = {
          title  = "Live-scores Lambda Errors"
          alarms = [aws_cloudwatch_metric_alarm.live_scores_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 14
        width  = 8
        height = 4
        properties = {
          title  = "Schedule-sync Lambda Errors"
          alarms = [aws_cloudwatch_metric_alarm.schedule_sync_errors.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 14
        width  = 8
        height = 4
        properties = {
          title  = "Ingest Lambda Throttles"
          alarms = [aws_cloudwatch_metric_alarm.ingest_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 14
        width  = 8
        height = 4
        properties = {
          title  = "Normalize Lambda Throttles"
          alarms = [aws_cloudwatch_metric_alarm.normalize_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 18
        width  = 8
        height = 4
        properties = {
          title  = "Live-scores Lambda Throttles"
          alarms = [aws_cloudwatch_metric_alarm.live_scores_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 18
        width  = 8
        height = 4
        properties = {
          title  = "Predict Lambda Throttles"
          alarms = [aws_cloudwatch_metric_alarm.predict_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 18
        width  = 8
        height = 4
        properties = {
          title  = "Predict-read Lambda Throttles"
          alarms = [aws_cloudwatch_metric_alarm.predict_read_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 22
        width  = 8
        height = 4
        properties = {
          title  = "Schedule-sync Lambda Throttles"
          alarms = [aws_cloudwatch_metric_alarm.schedule_sync_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 22
        width  = 8
        height = 4
        properties = {
          title  = "Predict Duration p99"
          alarms = [aws_cloudwatch_metric_alarm.predict_duration_p99.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 22
        width  = 8
        height = 4
        properties = {
          title  = "Predict-read Duration p99"
          alarms = [aws_cloudwatch_metric_alarm.predict_read_duration_p99.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 26
        width  = 8
        height = 4
        properties = {
          title  = "DynamoDB Read Throttles"
          alarms = [aws_cloudwatch_metric_alarm.dynamodb_read_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 26
        width  = 8
        height = 4
        properties = {
          title  = "DynamoDB Write Throttles"
          alarms = [aws_cloudwatch_metric_alarm.dynamodb_write_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 26
        width  = 8
        height = 4
        properties = {
          title  = "Ingest Orchestrator: Execution Failed"
          alarms = [aws_cloudwatch_metric_alarm.ingest_orchestrator_failed.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 30
        width  = 8
        height = 4
        properties = {
          title  = "Ingest Orchestrator: Execution Timed Out"
          alarms = [aws_cloudwatch_metric_alarm.ingest_orchestrator_timed_out.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 30
        width  = 8
        height = 4
        properties = {
          title  = "API Gateway 4XX Rate (%)"
          alarms = [aws_cloudwatch_metric_alarm.api_gateway_4xx_rate.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 30
        width  = 8
        height = 4
        properties = {
          title  = "API Gateway Latency p99"
          alarms = [aws_cloudwatch_metric_alarm.api_gateway_latency_p99.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 34
        width  = 8
        height = 4
        properties = {
          title  = "CloudFront 5xx Error Rate"
          alarms = [aws_cloudwatch_metric_alarm.cloudfront_5xx_rate.arn]
        }
      },
    ]
  })
}
