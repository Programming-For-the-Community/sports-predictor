# Backend infrastructure health: API Gateway, DynamoDB, Step Functions,
# ECS/Fargate. Merges what were 4 separate dashboards (api-gateway,
# dynamodb, step-functions, ecs-fargate) into one.
locals {
  dynamodb_dashboard_table_prefix = "${var.project}-"
}

resource "aws_cloudwatch_dashboard" "platform" {
  dashboard_name = "${var.project}-platform"

  dashboard_body = jsonencode({
    widgets = [
      # --- API Gateway ---
      {
        type       = "text", x = 0, y = 0, width = 24, height = 1
        properties = { markdown = "## API Gateway" }
      },
      {
        type   = "metric"
        x      = 0
        y      = 1
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
        y      = 1
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "4XX / 5XX errors (a 429 throttle shows up as 4XXError)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ApiGateway", "4XXError", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Sum", label = "4XX" }],
            ["AWS/ApiGateway", "5XXError", "ApiName", aws_api_gateway_rest_api.main.name, "Stage", aws_api_gateway_stage.main.stage_name, { stat = "Sum", label = "5XX" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 7
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
        y      = 7
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

      # --- DynamoDB ---
      {
        type       = "text", x = 0, y = 13, width = 24, height = 1
        properties = { markdown = "## DynamoDB" }
      },
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Consumed read capacity by table"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/DynamoDB,TableName} ConsumedReadCapacityUnits ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)", id = "rcu_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 14
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Consumed write capacity by table"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/DynamoDB,TableName} ConsumedWriteCapacityUnits ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)", id = "wcu_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 20
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Throttled requests by table"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/DynamoDB,TableName} ThrottledRequests ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)", id = "throttled_all" }]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 20
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Throttled requests total (bar)"
          view    = "bar"
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/DynamoDB,TableName} ThrottledRequests ${local.dynamodb_dashboard_table_prefix}', 'Sum', 300)", id = "throttled_bar" }]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 26
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Successful request latency by table (ms, average)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [[{ expression = "SEARCH('{AWS/DynamoDB,TableName} SuccessfulRequestLatency ${local.dynamodb_dashboard_table_prefix}', 'Average', 300)", id = "latency_all" }]]
        }
      },

      # --- Step Functions ---
      {
        type       = "text", x = 0, y = 33, width = 24, height = 1
        properties = { markdown = "## Step Functions" }
      },
      {
        type   = "metric"
        x      = 0
        y      = 34
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Ingest orchestrator -- executions"
          view    = "timeSeries"
          stacked = false
          period  = 86400
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Failed" }],
            ["AWS/States", "ExecutionsTimedOut", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Timed out" }],
            ["AWS/States", "ExecutionsAborted", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Sum", label = "Aborted" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 34
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Training orchestrator -- executions"
          view    = "timeSeries"
          stacked = false
          period  = 2592000
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Failed" }],
            ["AWS/States", "ExecutionsTimedOut", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Timed out" }],
            ["AWS/States", "ExecutionsAborted", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Sum", label = "Aborted" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 40
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Ingest orchestrator -- execution time (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 86400
          metrics = [
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Average", label = "Average" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.ingest_orchestrator.arn, { stat = "Maximum", label = "Max" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 40
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Training orchestrator -- execution time (ms)"
          view    = "timeSeries"
          stacked = false
          period  = 2592000
          metrics = [
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Average", label = "Average" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.training_orchestrator.arn, { stat = "Maximum", label = "Max" }],
          ]
        }
      },

      # --- ECS / Fargate ---
      {
        type       = "text", x = 0, y = 47, width = 24, height = 1
        properties = { markdown = "## ECS / Fargate" }
      },
      {
        type   = "metric"
        x      = 0
        y      = 48
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Cluster CPU utilization (%)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, { stat = "Average", label = "Average" }],
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, { stat = "Maximum", label = "Max" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 48
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "Cluster memory utilization (%)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/ECS", "MemoryUtilization", "ClusterName", aws_ecs_cluster.main.name, { stat = "Average", label = "Average" }],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", aws_ecs_cluster.main.name, { stat = "Maximum", label = "Max" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 54
        width  = 24
        height = 6
        properties = {
          region  = var.region
          title   = "Running / pending tasks"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", aws_ecs_cluster.main.name, { stat = "Maximum", label = "Running" }],
            ["ECS/ContainerInsights", "PendingTaskCount", "ClusterName", aws_ecs_cluster.main.name, { stat = "Maximum", label = "Pending" }],
          ]
        }
      },
    ]
  })
}