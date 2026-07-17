# One cluster for every sport's Fargate tasks (backfill, feature
# engineering, training, and eventually recurring ingest). An ECS cluster
# is a free logical grouping -- cost only comes from tasks actually
# running inside it -- so there's no reason for per-sport clusters.
resource "aws_ecs_cluster" "main" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled" # avoid the extra CloudWatch charge until it's actually needed for debugging
  }

  tags = merge(local.common_tags, {
    Sport     = "shared"
    Component = "orchestration"
  })
}
