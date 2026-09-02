# DynamoDB health dashboard: consumed capacity and throttling across all
# six tables (dynamodb-entities.tf, dynamodb-events.tf, dynamodb-player-
# game-stats.tf, dynamodb-team-game-stats.tf, dynamodb-predictions.tf,
# dynamodb-sport-registry.tf), built from AWS/DynamoDB namespace metrics.
#
# Uses a SEARCH() by table-name prefix ("${var.project}-"), same
# discovery technique cloudwatch-dashboard-lambda-observability.tf uses
# for Lambdas -- covers every table without referencing each
# aws_dynamodb_table resource individually, so a future table appears
# here automatically with no dashboard edit required. Every table is
# PAY_PER_REQUEST (on-demand) billing, which still throttles under a
# sudden burst or hot partition.
locals {
  dynamodb_dashboard_table_prefix = "${var.project}-"
}

resource "aws_cloudwatch_dashboard" "dynamodb" {
  dashboard_name = "${var.project}-dynamodb"

  dashboard_body = jsonencode({
    widgets = [
      # --- Row 1: Consumed capacity ---
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Consumed read capacity by table"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/DynamoDB,TableName} ConsumedReadCapacityUnits ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)"
                id         = "rcu_all"
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
          title   = "Consumed write capacity by table"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/DynamoDB,TableName} ConsumedWriteCapacityUnits ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)"
                id         = "wcu_all"
              }
            ]
          ]
        }
      },

      # --- Row 2: Throttling -- the real symptom to watch on-demand
      # billing for, since there's no fixed provisioned ceiling to alarm
      # against instead. ---
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Throttled requests by table"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/DynamoDB,TableName} ThrottledRequests ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)"
                id         = "throttled_all"
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
          title  = "Throttled requests total (bar)"
          view   = "bar"
          period = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/DynamoDB,TableName} ThrottledRequests ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)"
                id         = "throttled_bar"
              }
            ]
          ]
        }
      },

      # --- Row 3: Latency -- a slow table (large item, hot partition,
      # or a missing/inefficient GSI) shows up here before it shows up
      # as a Lambda timeout. ---
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Successful request latency by table (ms, average)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{AWS/DynamoDB,TableName} SuccessfulRequestLatency ${local.dynamodb_dashboard_table_prefix}', 'Average', 300)"
                id         = "latency_all"
              }
            ]
          ]
        }
      },
    ]
  })
}
