# Alarm-state dashboard for cloudwatch-alarms.tf's 18 alarms -- the first
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
          title  = "Lambda Throttles (all functions)"
          alarms = [aws_cloudwatch_metric_alarm.lambda_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 14
        width  = 8
        height = 4
        properties = {
          title  = "Predict + Predict-read Duration p99"
          alarms = [aws_cloudwatch_metric_alarm.predict_path_duration_p99.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 18
        width  = 8
        height = 4
        properties = {
          title  = "DynamoDB Throttles (all tables)"
          alarms = [aws_cloudwatch_metric_alarm.dynamodb_throttles.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 18
        width  = 8
        height = 4
        properties = {
          title  = "Ingest Orchestrator: Execution Failed"
          alarms = [aws_cloudwatch_metric_alarm.ingest_orchestrator_failed.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 18
        width  = 8
        height = 4
        properties = {
          title  = "Ingest Orchestrator: Execution Timed Out"
          alarms = [aws_cloudwatch_metric_alarm.ingest_orchestrator_timed_out.arn]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 22
        width  = 8
        height = 4
        properties = {
          title  = "API Gateway 4XX Rate (%)"
          alarms = [aws_cloudwatch_metric_alarm.api_gateway_4xx_rate.arn]
        }
      },
      {
        type   = "alarm"
        x      = 8
        y      = 22
        width  = 8
        height = 4
        properties = {
          title  = "API Gateway Latency p99"
          alarms = [aws_cloudwatch_metric_alarm.api_gateway_latency_p99.arn]
        }
      },
      {
        type   = "alarm"
        x      = 16
        y      = 22
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
