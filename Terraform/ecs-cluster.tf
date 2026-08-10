# One cluster for every sport's Fargate tasks (backfill, feature
# engineering, training). An ECS cluster is a free logical grouping --
# cost only comes from tasks actually running inside it -- so there's no
# reason for per-sport clusters.
resource "aws_ecs_cluster" "main" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "enhanced" # task/container-level CPU/memory/network/storage metrics in CloudWatch, not just cluster/service aggregates -- small added charge, worth it for sizing decisions
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

# Enables FARGATE_SPOT on the cluster -- a separate resource from
# aws_ecs_cluster itself; without this, a task's CapacityProviderStrategy
# can't reference FARGATE_SPOT at all. default_capacity_provider_strategy
# only applies to a task that specifies NEITHER LaunchType nor its own
# CapacityProviderStrategy -- every task definition's RunTask call in this
# project sets one or the other explicitly (see sfn-training-orchestrator.
# tf's RunTrainingTask for the one that opts into FARGATE_SPOT), so this
# default is inert in practice, just required by the resource itself.
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}
