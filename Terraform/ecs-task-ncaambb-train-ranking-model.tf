# 30-day retention so logs don't grow unbounded.
resource "aws_cloudwatch_log_group" "ncaambb_train_ranking_model" {
  name              = "/ecs/${var.project}-ncaambb-train-ranking-model"
  retention_in_days = 30

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "training"
  })
}

# Standalone Fargate task for the National Ranking (AP Top 25) model,
# team-poll-level rather than event-level or player-level -- used by
# season_simulation.py's March Madness/conference-tournament field
# selection (see project-ncaambb-onboarding memory), not by any
# game-outcome prediction.
#
# Its own image, not the win-probability task's -- team-poll feature
# building is a distinct, smaller pipeline from the event-level
# event_features.parquet the other three training tasks share, so it gets
# its own Dockerfile rather than a fourth command override on that image
# (same reasoning as NCAAFB's own ranking model task).
#
# Uses the shared aws_iam_role.ecs_pipeline. Same cpu/memory sizing
# (locals-training-compute.tf) as every other training task.
resource "aws_ecs_task_definition" "ncaambb_train_ranking_model" {
  family                   = "${var.project}-ncaambb-train-ranking-model"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = local.training_task_cpu
  memory                   = local.training_task_memory
  execution_role_arn       = aws_iam_role.ecs_pipeline.arn
  task_role_arn            = aws_iam_role.ecs_pipeline.arn

  container_definitions = jsonencode([
    {
      name      = "ncaambb-train-ranking-model"
      image     = "${var.ecr_repo_url}:ncaambb-train-ranking-model-latest"
      essential = true
      environment = [
        { name = "MODEL_ARTIFACTS_BUCKET_NAME", value = aws_s3_bucket.model_artifacts.bucket },
        { name = "AWS_REGION", value = var.region },
        # BLAS-oversubscription guard: caps thread pools so scikit-learn
        # doesn't over-parallelize on a bounded-vCPU Fargate task.
        { name = "OMP_NUM_THREADS", value = "1" },
        { name = "OPENBLAS_NUM_THREADS", value = "1" },
        { name = "MKL_NUM_THREADS", value = "1" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ncaambb_train_ranking_model.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "train-ranking-model"
        }
      }
    }
  ])

  tags = merge(local.common_tags, {
    Sport     = "ncaambb"
    Component = "training"
  })
}
