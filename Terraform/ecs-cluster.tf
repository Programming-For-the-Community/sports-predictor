# One cluster for every sport's Fargate tasks (backfill, feature
# engineering, training). An ECS cluster is a free logical grouping --
# cost only comes from tasks actually running inside it -- so there's no
# reason for per-sport clusters.
resource "aws_ecs_cluster" "main" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "enhanced" # task/container-level CPU/memory/network/storage metrics in CloudWatch
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}

# Enables FARGATE_SPOT on the cluster; without this a task's
# CapacityProviderStrategy can't reference FARGATE_SPOT at all. Also
# registers the EC2 training track's two capacity providers
# (ec2-training-asg.tf) -- additive, default_capacity_provider_strategy
# stays FARGATE, so every task that doesn't specify its own
# CapacityProviderStrategy (i.e. everything except training) is
# completely unaffected by their presence in this list.
# default_capacity_provider_strategy only applies to a task that specifies
# neither LaunchType nor its own CapacityProviderStrategy.
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name
  capacity_providers = [
    "FARGATE",
    "FARGATE_SPOT",
    aws_ecs_capacity_provider.ec2_training_spot.name,
    aws_ecs_capacity_provider.ec2_training_ondemand.name,
  ]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}
