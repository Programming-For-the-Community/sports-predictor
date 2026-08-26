# ECS/Fargate resource-utilization dashboard for the shared cluster
# (ecs-cluster.tf) every sport's backfill/feature-engineering/training
# task runs in. Cluster-wide widgets use the classic, always-published
# AWS/ECS namespace; per-task-definition-family widgets use
# ECS/ContainerInsights, available because ecs-cluster.tf already has
# `containerInsights = "enhanced"` turned on (no Terraform change needed
# for this dashboard to work).
#
# Added 2026-08-25 specifically because there was previously ZERO
# resource-utilization visibility into any Fargate task run -- there's
# already been one real production OOM kill here (a RandomForest
# candidate hit a genuine memory ceiling at 64GB during a live NCAA MBB
# training run, 2026-08-22 -- see library/ml/model_types.py's own
# comment on _RF_PARAM_DISTRIBUTIONS), discovered only after the fact
# from the task simply dying. A per-task-family memory widget is exactly
# what would have made that visible in real time instead.
#
# CAUTION -- the per-task-family widgets below (TaskCpuUtilization/
# TaskMemoryUtilization) are AWS's own documented metric names for ECS
# Container Insights with enhanced observability, but were NOT
# independently confirmed against this account's actually-emitted
# metrics (no live AWS access from this environment -- see this repo's
# own "verify real fields before writing code" convention, which
# normally applies to API responses, not CloudWatch metric schemas, but
# the same honesty applies here). If a widget renders empty after apply,
# check `aws cloudwatch list-metrics --namespace ECS/ContainerInsights`
# for the real metric name and fix it here -- an empty graph fails safe
# (no Terraform error, nothing breaks), it just means this comment's
# guess needs a one-line correction.
resource "aws_cloudwatch_dashboard" "ecs_fargate" {
  dashboard_name = "${var.project}-ecs-fargate"

  dashboard_body = jsonencode({
    widgets = [
      # --- Row 1: Cluster-wide CPU/memory (AWS/ECS -- always published,
      # no Container Insights required) ---
      {
        type   = "metric"
        x      = 0
        y      = 0
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
        y      = 0
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

      # --- Row 2: Task counts (classic Container Insights, well-
      # established) ---
      {
        type   = "metric"
        x      = 0
        y      = 6
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

      # --- Row 3: Per-task-definition-family CPU/memory (enhanced
      # Container Insights) -- what actually would have caught the real
      # RandomForest OOM this dashboard's own header comment describes.
      # Discovers every sport's backfill/feature-engineering/train-*
      # task family automatically via SEARCH, same as the Lambda/
      # DynamoDB dashboards' own prefix-discovery technique. ---
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          region  = var.region
          title   = "CPU utilization by task family (%) -- SEE THIS FILE'S OWN CAUTION COMMENT"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{ECS/ContainerInsights,ClusterName,TaskDefinitionFamily} TaskCpuUtilization ClusterName=\"${aws_ecs_cluster.main.name}\"', 'Average', 300)"
                id         = "cpu_by_family"
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
          title   = "Memory utilization by task family (%) -- SEE THIS FILE'S OWN CAUTION COMMENT"
          view    = "timeSeries"
          stacked = false
          period  = 300
          metrics = [
            [
              {
                expression = "SEARCH('{ECS/ContainerInsights,ClusterName,TaskDefinitionFamily} TaskMemoryUtilization ClusterName=\"${aws_ecs_cluster.main.name}\"', 'Average', 300)"
                id         = "mem_by_family"
              }
            ]
          ]
        }
      },
    ]
  })
}
