# Backend infrastructure health: API Gateway, DynamoDB, Step Functions,
# ECS/Fargate, and security & access (WAF, API access logs, CloudFront
# edge logs, VPC flow logs). Merges what were 4 separate dashboards
# (api-gateway, dynamodb, step-functions, ecs-fargate) into one.
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
      # --- Security & access ---
      {
        type       = "text", x = 0, y = 60, width = 24, height = 1
        properties = { markdown = "## Security & access" }
      },
      {
        type   = "metric"
        x      = 0
        y      = 61
        width  = 12
        height = 6
        properties = {
          # CloudFront-scoped WAF metrics only exist in us-east-1.
          region  = "us-east-1"
          title   = "CloudFront WAF -- allowed vs blocked (IP reputation, per-IP rate limit)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/WAFV2", "AllowedRequests", "WebACL", aws_wafv2_web_acl.cloudfront.name, "Rule", "ALL", { stat = "Sum", label = "Allowed" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.cloudfront.name, "Rule", "${var.project}-amazon-ip-reputation-list", { stat = "Sum", label = "Blocked -- IP reputation" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.cloudfront.name, "Rule", "${var.project}-per-ip-rate-limit", { stat = "Sum", label = "Blocked -- per-IP rate limit" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 61
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "API Gateway WAF -- via CloudFront vs direct (blocked)"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            ["AWS/WAFV2", "AllowedRequests", "WebACL", aws_wafv2_web_acl.api_gateway.name, "Region", var.region, "Rule", "ALL", { stat = "Sum", label = "Allowed (via CloudFront)" }],
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.api_gateway.name, "Region", var.region, "Rule", "ALL", { stat = "Sum", label = "Blocked (direct to API)" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 67
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "API responses by status class"
          view   = "timeSeries"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.api_gateway_access.name}'
            | fields concat("s", status) as s
            | stats sum(strcontains(s, "s2")) as ok_2xx, sum(strcontains(s, "s4")) as client_4xx, sum(strcontains(s, "s5")) as server_5xx by bin(5m)
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 67
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Top API users (Cognito sub) -- requests and 4xx"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.api_gateway_access.name}'
            | fields concat("s", status) as s
            | stats count(*) as requests, sum(strcontains(s, "s4")) as client_4xx by userSub
            | sort requests desc
            | limit 10
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 73
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "API latency by route (ms)"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.api_gateway_access.name}'
            | stats count(*) as requests, pct(responseLatency, 50) as p50, pct(responseLatency, 95) as p95 by httpMethod, resourcePath
            | sort p95 desc
            | limit 15
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 73
        width  = 12
        height = 6
        properties = {
          # CloudFront edge logs are delivered to us-east-1 (cloudfront-standard-logging.tf).
          region = "us-east-1"
          title  = "Top client IPs (CloudFront edge) -- requests and 4xx"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.cloudfront_edge_access_logs.name}'
            | fields concat("s", `sc-status`) as s
            | stats count(*) as requests, sum(strcontains(s, "s4")) as client_4xx by `c-ip`, `c-country`
            | sort requests desc
            | limit 10
          QUERY
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 79
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Rejected network connections (VPC flow logs)"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.vpc_flow_logs.name}'
            | stats count(*) as rejects by srcAddr, dstPort, protocol
            | sort rejects desc
            | limit 15
          QUERY
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 79
        width  = 12
        height = 6
        properties = {
          region = var.region
          title  = "Direct-to-API attempts blocked by the API WAF"
          view   = "table"
          query  = <<-QUERY
            SOURCE '${aws_cloudwatch_log_group.waf_api_gateway.name}'
            | stats count(*) as blocked by httpRequest.clientIp, httpRequest.country, httpRequest.uri
            | sort blocked desc
            | limit 15
          QUERY
        }
      },
    ]
  })
}