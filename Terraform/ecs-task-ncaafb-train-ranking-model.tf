# 30-day retention, same rationale as ecs-task-nfl-backfill.tf.
resource "aws_cloudwatch_log_group" "ncaafb_train_ranking_model" {
  name              = "/ecs/${var.project}-ncaafb-train-ranking-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "training"
  })
}

# Standalone Fargate task for the National Ranking (1-25) model -- a new
# model shape for this project, team-week-level rather than event-level or
# player-level (see project memory's NCAAFB onboarding plan). Trained only
# on ranked-team-weeks (CFBD AP Poll rank as the label); at serving time
# every FBS team's predicted score is sorted and the lowest 25 become the
# projected Top 25.
#
# Its own image, not ecs-task-ncaafb-train-win-probability-model.tf's --
# team-week feature building (Source/feature-engineering/ncaafb's ranking
# step) is a distinct, smaller pipeline from the event-level
# event_features.parquet the other three training tasks share, so it gets
# its own Dockerfile rather than a fourth command override on that image.
#
# Uses the shared aws_iam_role.ecs_pipeline (iam-ecs-pipeline.tf). Same
# cpu/memory sizing (locals-training-compute.tf) as every other training
# task -- one shared vCPU budget across all of a sport's targets.
resource "aws_ecs_task_definition" "ncaafb_train_ranking_model" {
  family                   = "${var.project}-ncaafb-train-ranking-model"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "ncaafb-train-ranking-model"
      image     = "${var.ecr_repo_url}:ncaafb-train-ranking-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # Same BLAS-oversubscription guard as
        # ecs-task-nfl-train-win-probability-model.tf's own comment.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ncaafb_train_ranking_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-ranking-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaa-fb"
    Component = "training"
  })
}
