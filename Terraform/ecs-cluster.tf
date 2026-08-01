# One cluster for every sport's Fargate tasks (backfill, feature
# engineering, training, and eventually recurring ingest). An ECS cluster
# is a free logical grouping -- cost only comes from tasks actually
# running inside it -- so there's no reason for per-sport clusters.
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
